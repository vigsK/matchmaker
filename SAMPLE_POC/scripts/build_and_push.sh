#!/usr/bin/env bash
# Trigger the CodeBuild project that builds the custom image and pushes it to ECR,
# then wait for it to finish.
set -euo pipefail
: "${AWS_PROFILE:=sandbox1}"; export AWS_PROFILE
: "${AWS_DEFAULT_REGION:=us-east-1}"; export AWS_DEFAULT_REGION

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/infra"

PROJECT="$(terraform output -raw codebuild_project)"
echo ">> Starting CodeBuild project: $PROJECT"
BUILD_ID="$(aws codebuild start-build --project-name "$PROJECT" --query 'build.id' --output text)"
echo ">> Build id: $BUILD_ID"

while true; do
  read -r STATUS PHASE < <(aws codebuild batch-get-builds --ids "$BUILD_ID" \
    --query 'builds[0].[buildStatus,currentPhase]' --output text)
  echo "   status=$STATUS phase=$PHASE"
  case "$STATUS" in
    SUCCEEDED) echo ">> Image built and pushed."; break ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      echo "!! Build $STATUS - see CloudWatch logs /codebuild/*"; exit 1 ;;
  esac
  sleep 15
done
