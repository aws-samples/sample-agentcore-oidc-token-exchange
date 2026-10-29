#!/usr/bin/env python3
"""Create a Keycloak server on a small EC2 instance (idempotent).

Docker (Keycloak + Postgres + Caddy with automatic Let's Encrypt), a public HTTPS issuer under a Route 53 hosted
zone you control. AgentCore Gateway fetches the OIDC discovery document and JWKS from the public internet, so the
issuer needs a publicly trusted certificate. Everything is tagged project=<SAMPLE_PREFIX>.

  Required env   KC_HOSTNAME        e.g. keycloak.example.com (a name inside the hosted zone below)
                 KC_HOSTED_ZONE_ID  Route 53 hosted zone id; only a NEW A record for KC_HOSTNAME is created,
                                    existing records are never changed
  Optional env   KC_REGION (us-west-2)  KC_INSTANCE_TYPE (t3.medium)  KC_VERSION (26.8.1)
                 KC_ADMIN_CIDR (this host's public IP /32): the only source allowed to reach
                 /admin*, /realms/master*, /metrics*, /health*
The instance always goes into the default VPC of KC_REGION (it is launched without a subnet id, so a custom VPC is
not supported). KC_ADMIN_CIDR, KC_VERSION and KC_INSTANCE_TYPE only take effect when the instance is first created;
rerunning this script does not change an existing instance (see README).
Public: /realms/<realm> (discovery, JWKS, token endpoints) on 443, 80 for the ACME challenge.
No SSH port; use scripts/ssm.sh.
The admin password is generated into Secrets Manager (<prefix>/keycloak/admin);
in Secrets Manager the instance role may read only that secret (it also has AmazonSSMManagedInstanceCore).
Usage: python idp/keycloak/infra.py     -> prints the issuer base URL
"""

import json
import os
import pathlib
import secrets
import string
import sys

import boto3

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from lib import idp, net, wait

HOST, ZONE = os.environ["KC_HOSTNAME"], os.environ["KC_HOSTED_ZONE_ID"]
REGION = os.environ.get("KC_REGION", "us-west-2")
KC_VERSION, ITYPE = os.environ.get("KC_VERSION", "26.8.1"), os.environ.get("KC_INSTANCE_TYPE", "t3.medium")
P = idp.PREFIX
NAME = f"{P}-kc"
admin_cidr = (
    os.environ.get("KC_ADMIN_CIDR")
    or net.open_https("https://checkip.amazonaws.com", timeout=10).read().decode().strip() + "/32"
)
tags = [{"Key": "project", "Value": P}]
ec2, iam, r53 = boto3.client("ec2", region_name=REGION), boto3.client("iam"), boto3.client("route53")
acct = boto3.client("sts").get_caller_identity()["Account"]
sm = boto3.client("secretsmanager", region_name=idp.SECRETS_REGION)
ADMIN_SECRET = idp.secret_id("keycloak", "admin")

try:
    sm.describe_secret(SecretId=ADMIN_SECRET)
except sm.exceptions.ResourceNotFoundException:
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    sm.create_secret(Name=ADMIN_SECRET, SecretString=json.dumps({"username": "admin", "password": pw}), Tags=tags)

try:
    iam.get_role(RoleName=NAME)
except iam.exceptions.NoSuchEntityException:
    iam.create_role(
        RoleName=NAME,
        Tags=tags,
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}
                ],
            }
        ),
    )
    iam.attach_role_policy(RoleName=NAME, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    iam.create_instance_profile(InstanceProfileName=NAME, Tags=tags)
    iam.add_role_to_instance_profile(InstanceProfileName=NAME, RoleName=NAME)
iam.put_role_policy(
    RoleName=NAME,
    PolicyName="read-admin-secret",
    PolicyDocument=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": f"arn:aws:secretsmanager:{idp.SECRETS_REGION}:{acct}:secret:{ADMIN_SECRET}-*",
                }
            ],
        }
    ),
)

vpc = next(v["VpcId"] for v in ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"])
sg = ec2.describe_security_groups(
    Filters=[{"Name": "group-name", "Values": [NAME]}, {"Name": "vpc-id", "Values": [vpc]}]
)["SecurityGroups"]
if sg:
    sg_id = sg[0]["GroupId"]
else:
    sg_id = ec2.create_security_group(
        GroupName=NAME,
        Description="Keycloak sample: 80/443 only",
        VpcId=vpc,
        TagSpecifications=[{"ResourceType": "security-group", "Tags": tags}],
    )["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": p, "ToPort": p, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]} for p in (80, 443)
        ],
    )

USER_DATA = f"""#!/bin/bash
set -eux
apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 python3-boto3
mkdir -p /opt/kc && cd /opt/kc && umask 077
python3 - <<'PY'
import boto3, json, secrets, string
s = json.loads(boto3.client("secretsmanager", \
region_name="{idp.SECRETS_REGION}").get_secret_value(SecretId="{ADMIN_SECRET}")["SecretString"])
db = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
open(".env", "w").write("KC_ADMIN=%s\\nKC_ADMIN_PW=%s\\nDB_PW=%s\\n" % (s["username"], s["password"], db))
PY
cat > Caddyfile <<'EOF'
{HOST} {{
  @private {{
    path /admin* /realms/master* /metrics* /health* /resources/*/admin*
    not remote_ip {admin_cidr}
  }}
  respond @private 403
  reverse_proxy keycloak:8080
}}
EOF
cat > docker-compose.yml <<'EOF'
services:
  db:
    image: postgres:16
    restart: always
    environment: {{POSTGRES_DB: keycloak, POSTGRES_USER: keycloak, POSTGRES_PASSWORD: "${{DB_PW}}"}}
    volumes: [pgdata:/var/lib/postgresql/data]
  keycloak:
    image: quay.io/keycloak/keycloak:{KC_VERSION}
    restart: always
    command: start --http-enabled=true --proxy-headers=xforwarded --hostname=https://{HOST} \
--db=postgres --db-url=jdbc:postgresql://db/keycloak --db-username=keycloak \
--db-password=${{DB_PW}} --health-enabled=true
    environment: {{KC_BOOTSTRAP_ADMIN_USERNAME: "${{KC_ADMIN}}", KC_BOOTSTRAP_ADMIN_PASSWORD: "${{KC_ADMIN_PW}}"}}
    depends_on: [db]
  caddy:
    image: caddy:2
    restart: always
    ports: ["80:80", "443:443"]
    volumes: [./Caddyfile:/etc/caddy/Caddyfile, caddy_data:/data]
    depends_on: [keycloak]
volumes: {{pgdata: {{}}, caddy_data: {{}}}}
EOF
docker compose up -d
"""

ami = boto3.client("ssm", region_name=REGION).get_parameter(
    Name="/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
)["Parameter"]["Value"]
found = ec2.describe_instances(
    Filters=[
        {"Name": "tag:Name", "Values": [NAME]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]},
    ]
)["Reservations"]
if found:
    iid = found[0]["Instances"][0]["InstanceId"]
else:
    iam.get_waiter("instance_profile_exists").wait(InstanceProfileName=NAME)
    # A just-created instance profile can take a few seconds before EC2 accepts it.
    iid = wait.retry(
        lambda: ec2.run_instances(
            ImageId=ami,
            InstanceType=ITYPE,
            MinCount=1,
            MaxCount=1,
            SecurityGroupIds=[sg_id],
            UserData=USER_DATA,
            IamInstanceProfile={"Name": NAME},
            MetadataOptions={"HttpTokens": "required"},
            BlockDeviceMappings=[
                {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 20, "VolumeType": "gp3", "Encrypted": True}}
            ],
            TagSpecifications=[
                {"ResourceType": r, "Tags": tags + [{"Key": "Name", "Value": NAME}]} for r in ("instance", "volume")
            ],
        ),
        lambda e: (
            getattr(e, "response", {}).get("Error", {}).get("Code") == "InvalidParameterValue"
            and "iamInstanceProfile" in str(e)
        ),
        "the new instance profile to become usable by EC2",
    )["Instances"][0]["InstanceId"]
ec2.get_waiter("instance_running").wait(InstanceIds=[iid])

eip = idp.claim_keycloak_eip(ec2, NAME, tags)
ec2.associate_address(InstanceId=iid, AllocationId=eip["AllocationId"])

existing = r53.list_resource_record_sets(HostedZoneId=ZONE, StartRecordName=HOST, StartRecordType="A", MaxItems="1")[
    "ResourceRecordSets"
]
if (
    existing
    and existing[0]["Name"].rstrip(".") == HOST
    and existing[0].get("ResourceRecords", [{}])[0].get("Value") != eip["PublicIp"]
):
    sys.exit("refusing to change an existing DNS record")
if not (existing and existing[0]["Name"].rstrip(".") == HOST):
    r53.change_resource_record_sets(
        HostedZoneId=ZONE,
        ChangeBatch={
            "Comment": P,
            "Changes": [
                {
                    "Action": "CREATE",
                    "ResourceRecordSet": {
                        "Name": HOST,
                        "Type": "A",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": eip["PublicIp"]}],
                    },
                }
            ],
        },
    )
json.dump(
    {
        "instanceId": iid,
        "securityGroup": sg_id,
        "ip": eip["PublicIp"],
        "allocationId": eip["AllocationId"],
        "host": HOST,
    },
    open(idp.state_file("keycloak", "infra"), "w", encoding="utf-8"),
    indent=1,
)
print(f"https://{HOST}  instance {iid}  ip {eip['PublicIp']}")
