#!/usr/bin/env bash
# Tear everything down. S3 (force_destroy) and ECR (force_delete) are emptied
# automatically; secrets use a 0-day recovery window.
set -euo pipefail
: "${AWS_PROFILE:=sandbox1}"; export AWS_PROFILE
: "${AWS_DEFAULT_REGION:=us-east-1}"; export AWS_DEFAULT_REGION

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/infra"
terraform destroy -auto-approve
echo ">> Destroyed."
