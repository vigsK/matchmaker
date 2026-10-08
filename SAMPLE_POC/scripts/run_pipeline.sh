#!/usr/bin/env bash
# Start one Step Functions execution of the consistency workflow, wait for it to
# finish, and print the resulting report.json from S3.
set -euo pipefail
: "${AWS_PROFILE:=sandbox1}"; export AWS_PROFILE
: "${AWS_DEFAULT_REGION:=us-east-1}"; export AWS_DEFAULT_REGION

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/infra"

SM="$(terraform output -raw state_machine_arn)"
BUCKET="$(terraform output -raw s3_bucket)"
RUN_ID="run-$(date +%Y%m%d-%H%M%S)"

echo ">> Starting execution $RUN_ID"
EXEC_ARN="$(aws stepfunctions start-execution \
  --state-machine-arn "$SM" --name "$RUN_ID" --input '{}' \
  --query 'executionArn' --output text)"
echo ">> Execution ARN: $EXEC_ARN"

while true; do
  ST="$(aws stepfunctions describe-execution --execution-arn "$EXEC_ARN" \
        --query 'status' --output text)"
  echo "   status=$ST"
  case "$ST" in
    RUNNING) sleep 15 ;;
    SUCCEEDED) break ;;
    *) echo "!! Execution $ST"; exit 1 ;;
  esac
done

echo ">> Report (s3://$BUCKET/runs/$RUN_ID/report.json):"
aws s3 cp "s3://$BUCKET/runs/$RUN_ID/report.json" - 2>/dev/null | python3 -m json.tool || \
  echo "   (report not found - check the Step Functions execution)"
