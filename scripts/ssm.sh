#!/bin/sh
# Run a shell command on the Keycloak instance through SSM (the instance has no SSH port).  scripts/ssm.sh 'docker ps'
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STATE=${SAMPLE_STATE:-$ROOT/.state}   # the same directory the Python scripts use
IID=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['instanceId'])" "$STATE/keycloak-infra.json")
REGION=${KC_REGION:-us-west-2}
CID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" --document-name AWS-RunShellScript --parameters "commands=[\"$1\"]" --query Command.CommandId --output text)
sleep 3
until [ "$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" --query Status --output text)" != InProgress ]; do sleep 2; done
aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" --query '[StandardOutputContent,StandardErrorContent]' --output text
