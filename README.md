# sample-agentcore-oidc-token-exchange

[中文说明](README_CN.md)

Demonstrate an end user's OIDC identity through Amazon Bedrock AgentCore Gateway and
AgentCore Identity token exchange, using **Keycloak self-hosted on Amazon EC2**.
The downstream API echoes claims supplied by an **Amazon API Gateway HTTP API JWT authorizer**.
The Lambda function presents the identity claims already verified by the managed authorizer.

> **Disclaimer:** This repository is an example only and is NOT intended for production use.
> You are responsible for testing, securing and optimizing it for your requirements.
> It creates chargeable AWS resources; use a disposable sandbox with synthetic identities.

## Architecture

![Architecture](docs/architecture.png)

1. A test user signs in to Keycloak and obtains T1 with audience `gateway`.
2. The client invokes the Gateway MCP tool `resource___whoami` with T1.
3. The Gateway's managed `CUSTOM_JWT` authorizer checks the issuer's keys, audience
   and configured `azp=app` claim through AWS service configuration.
4. The Gateway requests a downstream token through AgentCore Identity.
5. Identity authenticates as Keycloak's confidential `gateway` client and performs
   RFC 8693 token exchange. T2 has audience `resource-api` and scope `resource.read`.
6. The Gateway invokes `GET /v1/me` with T2.
7. API Gateway validates T2 with its native JWT authorizer and route scope.
   Lambda returns `requestContext.authorizer.jwt.claims` unchanged as JSON.

The response demonstrates the caller's subject and authorized party after exchange.
An identity satisfying the managed gate receives its own claims. Keycloak's token
issuance policy governs which identities can obtain qualifying tokens.
Do not add sensitive business data to the echo function without a separate access-control design.

| Path | Purpose |
|---|---|
| `resource-api/` | Standard-library-only identity echo, ZIP builder and managed JWT API deployment |
| `gateway/provision.py` | Identity credential provider, scoped Gateway role and one OpenAPI `whoami` tool |
| `idp/keycloak/` | AWS infrastructure, realm/client/scope/test-user setup and token-exchange adapter |
| `lib/` | Keycloak loading, state/secret helpers, HTTPS-only client and bounded waiting |
| `tests/test_*.py` | Offline handler, provisioning, helper and repository-hygiene tests |
| `tests/e2e/` | Explicitly invoked checks of an already deployed sandbox |
| `scripts/cleanup.py` | Dry-run-first deletion of the sample resources |
| `docs/architecture.drawio` | Editable source for the SVG and PNG diagram |

## Prerequisites

- Python 3.10+, AWS CLI v2 and AWS credentials allowed to provision the listed resources.
- AgentCore Gateway and Identity, API Gateway HTTP APIs with JWT authorization, Lambda,
  IAM, Secrets Manager and CloudWatch Logs in the selected application Region.
- A Route 53 hosted zone and an unused public DNS name for Keycloak.
- A default VPC in `KC_REGION`; EC2, Elastic IP, encrypted EBS and Systems Manager access.
- A clean, dedicated Keycloak realm. Setup adds its clients, scopes and users;
  the administrator is responsible for managing any other realm configuration.

Keycloak's discovery, JWKS and login/token endpoints must be reachable over trusted
public HTTPS by the managed services. Its admin paths are limited by `KC_ADMIN_CIDR`.
The test client uses password grants solely for disposable test identities.
Real user applications should use the IdP's supported authorization-code/PKCE flow.

## Deployment

```sh
git clone https://github.com/aws-samples/sample-agentcore-oidc-token-exchange.git
cd sample-agentcore-oidc-token-exchange
pip install -r requirements.txt

export KC_HOSTNAME=keycloak.example.com
export KC_HOSTED_ZONE_ID=<your-hosted-zone-id>
export KC_ADMIN_CIDR=<your-admin-public-ip>/32
export KC_REALM=sample
export SAMPLE_REGION=us-west-2

resource-api/build.sh .build
python idp/keycloak/infra.py
python idp/keycloak/setup.py
python resource-api/deploy.py .build/resource-api.zip
python gateway/provision.py
```

Wait for the Keycloak certificate before running setup, and run administrative commands
from the allowed CIDR. The Lambda archive contains no third-party packages.
The API deployment disables the default endpoint during changes, configures the JWT gate,
gates existing routes before uploading the echo code, removes routes other than `GET /v1/me`,
and enables the endpoint only after configuration succeeds. A failed deployment may leave
the endpoint disabled; correct the error and rerun. Custom domains and manually added
stages/integrations are outside this deployment script's supported configuration.

The HTTP API is named `<prefix>-keycloak-resource-api-jwt`. Deployment manages a dedicated
endpoint for the function. An API named `<prefix>-keycloak-resource-api`, if present,
is deleted while Lambda invocation permission is suspended. This maintenance can interrupt
service and change the endpoint URL. Rerun `gateway/provision.py` and update direct clients
after deployment.

| Variable | Default | Meaning |
|---|---|---|
| `IDP` | `keycloak` | The only supported IdP value |
| `SAMPLE_PREFIX` | `sample-oidc` | Application and AWS resource name prefix |
| `SAMPLE_REGION` / `SAMPLE_PROFILE` | `us-west-2` / default chain | Region and credentials for API, Gateway and Identity |
| `SECRETS_REGION` | `us-west-2` | IdP secrets, using the global-account default credential chain |
| `SAMPLE_STATE` | `.state` | Non-secret resource identifiers; use `.state-<suffix>` for another deployment |
| `KC_HOSTNAME`, `KC_HOSTED_ZONE_ID` | Required | Public issuer DNS name and Route 53 hosted zone |
| `KC_REGION` | `us-west-2` | Keycloak EC2 Region in the global `aws` partition |
| `KC_REALM` | `sample` | Dedicated realm; choose a different realm for a different prefix on the same server |
| `KC_INSTANCE_TYPE` / `KC_VERSION` | `t3.medium` / `26.8.1` | Applied when creating the instance |
| `KC_ADMIN_CIDR` | Detected public IP `/32` | Source allowed to access admin/master/health/metrics paths; creation-time setting |
| `SAMPLE_GATEWAY_DEBUG` | Unset | `1` exposes detailed Gateway errors; leave unset outside debugging |

Issuer, audience and route scope are returned by `resource_api_config()` in the Keycloak
adapter and supplied to the managed API configuration. No identity configuration is placed
in the Lambda environment. The inbound Gateway accepts only audience `gateway`.

### AWS China Regions

Application scripts retain `aws-cn` ARN construction through `SAMPLE_REGION=cn-north-1`
and an appropriate `SAMPLE_PROFILE`; use a separate `SAMPLE_STATE`.
Keycloak infrastructure and its source credentials remain in a global AWS account,
using the default credential chain and `SECRETS_REGION`. Cross-border HTTPS reachability
and organizational data policies are deployment prerequisites.

**HTTP API native JWT authorizers are available in `cn-north-1` and `cn-northwest-1`.**
API Gateway fetches the issuer's `/.well-known/openid-configuration` during authorizer
creation. The Keycloak discovery endpoint and the JWKS URI it advertises must be
publicly reachable from the China Region's API Gateway, with trusted HTTPS.
An issuer whose discovery endpoint cannot be reached from China, such as an unreachable
Google endpoint, cannot be used. Reachability from the operator's machine alone is insufficient.
Service availability does not establish that a particular Keycloak deployment or the
complete AgentCore token-exchange path works. Verify that path and regional dependencies
in the intended environment before relying on the managed authorization boundary.

## Usage

```python
import json
from lib import idp, mcp

adapter = idp.load()
with open(idp.state_file("keycloak", f"gateway-{idp.REGION}")) as state:
    gateway = json.load(state)["gatewayUrl"]
status, reply = mcp.tool(
    gateway, adapter.token("user-a", "gateway"), "resource___whoami", {}
)
```

The tool returns the managed authorizer's claims, including `sub` and `azp` when issued.
Treat the response as potentially sensitive identity data; do not log it unnecessarily.
Invoke the identity tool with an empty argument object.

## Checks

The local HTTPS transport tests require OpenSSL on PATH to generate temporary test certificates.

```sh
python -m unittest discover -s tests -v
pip install ruff==0.17.1 bandit==1.9.4 pre-commit==4.5.1
ruff check .
ruff format --check .
bandit -c pyproject.toml -r .
```

The pre-commit configuration also uses git-secrets; install it and register its AWS rules
before running `pre-commit run --all-files`.
Offline tests exercise the echo contract and AWS configuration calls.
For an explicitly authorized live deployment check:

```sh
python tests/e2e/test_resource_api.py
python tests/e2e/test_gateway.py
python tests/e2e/test_identity_direct.py
```

The last command creates a test workload identity. Live checks consume AWS services and
are not part of the offline suite. Denial checks must succeed before relying on the endpoint.

## Cleanup

```sh
python scripts/cleanup.py
python scripts/cleanup.py --execute --idp keycloak
```

`--region R` is repeatable. Without `--idp keycloak`, cleanup targets only application
resources in the selected Regions. With it, the EC2 instance and its realm data, Elastic
IP, security group, instance role/profile and Keycloak secrets are also deleted.
Set `KC_HOSTNAME`, `KC_HOSTED_ZONE_ID` and the original `KC_REGION` to remove the DNS record.
Secrets are deleted without a recovery window. No rollback of partial deletion is provided.
Resources are selected by exact prefixed names; do not reuse those names or project tags.
Listings use their first page, so the tool is for a small sandbox, not a general inventory.
The Gateway-managed workload identities and credential-provider secret are expected to be
deleted by their owning services; verify the final dry run and service inventories.
Failures are reported with a nonzero exit status. Other service logs and manually added
resources are outside cleanup coverage.

## Security, Cost and License

See [SECURITY.md](SECURITY.md) for direct-invocation risks, token caching, network exposure
and secrets. The managed authorizers and IAM permissions define the sample's access boundary.
Gateways, Identity, API Gateway, Lambda, Secrets Manager and logs are usage-billed; EC2,
EBS and public IPv4 addresses continue to incur charges until cleaned up.
See [AWS Pricing](https://aws.amazon.com/pricing/).

Licensed under [MIT-0](LICENSE). Third-party dependencies are listed in
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES). Contributions: [CONTRIBUTING](CONTRIBUTING.md).
