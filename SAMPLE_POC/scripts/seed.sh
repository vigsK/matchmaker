#!/usr/bin/env bash
# Run the one-time MODE=seed Fargate task to load identical synthetic data into
# both Aurora clusters. Waits for completion and checks the exit code.
set -euo pipefail
: "${AWS_PROFILE:=sandbox1}"; export AWS_PROFILE
: "${AWS_DEFAULT_REGION:=us-east-1}"; export AWS_DEFAULT_REGION

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/infra"

CLUSTER="$(terraform output -raw ecs_cluster_name)"
TASKDEF="$(terraform output -raw task_definition_arn)"
CONTAINER="$(terraform output -raw container_name)"
NETCFG="$(terraform output -raw run_task_network_config)"

echo ">> Launching seed task on $CLUSTER"
TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASKDEF" \
  --launch-type FARGATE \
  --network-configuration "$NETCFG" \
  --overrides "{\"containerOverrides\":[{\"name\":\"$CONTAINER\",\"environment\":[{\"name\":\"MODE\",\"value\":\"seed\"}]}]}" \
  --query 'tasks[0].taskArn' --output text)"
echo ">> Seed task: $TASK_ARN"

echo ">> Waiting for seed task to stop (this includes Aurora warm-up; can take several minutes)..."
# `wait` caps at ~10 min; loop until the task truly stops.
until [ "$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
        --query 'tasks[0].lastStatus' --output text)" = "STOPPED" ]; do
  sleep 15
  echo "   ...still running"
done

EXIT_CODE="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
REASON="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].stoppedReason' --output text)"
echo ">> Seed exit code: $EXIT_CODE (reason: $REASON)"
[ "$EXIT_CODE" = "0" ] || { echo "!! Seed failed - check logs /ecs/*"; exit 1; }
echo ">> Seed complete."
