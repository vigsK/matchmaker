# One-command, reproducible deployment of the query-consistency POC.
# Override the profile/region per account:  make deploy PROFILE=myprofile REGION=eu-west-1
PROFILE ?= sandbox1
REGION  ?= us-east-1

export AWS_PROFILE=$(PROFILE)
export AWS_DEFAULT_REGION=$(REGION)

TF = cd infra && terraform

.PHONY: help excel whoami reset init fmt validate plan apply build seed run report deploy destroy

help:
	@echo "Targets:"
	@echo "  excel     - regenerate the sample Excel from src/app/queries.py"
	@echo "  whoami    - verify the active credentials (aws sts get-caller-identity)"
	@echo "  reset     - wipe stale Terraform state from a previous (dead) sandbox account"
	@echo "  init      - terraform init"
	@echo "  validate  - terraform fmt -check + validate"
	@echo "  plan      - terraform plan"
	@echo "  apply     - terraform apply (provisions all AWS infra)"
	@echo "  build     - CodeBuild: build + push the image to ECR"
	@echo "  seed      - load identical data into both Aurora clusters"
	@echo "  run       - start one Step Functions run and print the report"
	@echo "  deploy    - excel + apply + build + seed (full bring-up)"
	@echo "  destroy   - tear everything down"
	@echo
	@echo "New 4h sandbox? Rotate all THREE credential fields, then 'make reset deploy':"
	@echo "  aws configure set aws_access_key_id     ASIA... --profile $(PROFILE)"
	@echo "  aws configure set aws_secret_access_key ...     --profile $(PROFILE)"
	@echo "  aws configure set aws_session_token     ...     --profile $(PROFILE)"

excel:
	python3 scripts/gen_excel.py

# Fails fast if creds are expired/missing the session token (InvalidClientTokenId).
# Prints the account so you can confirm you're pointed at the current sandbox.
whoami:
	@aws sts get-caller-identity --query 'Account' --output text \
		&& echo ">> Credentials OK for profile '$(PROFILE)' in $(REGION)." \
		|| { echo "!! Bad/expired creds for profile '$(PROFILE)'. Re-run 'aws configure set' for all THREE fields (incl. aws_session_token), then retry."; exit 1; }

# A 4h sandbox hands out a NEW account each session, but local state still points
# at the dead account's resources (account-scoped bucket name + ARNs). The old
# account is already wiped, so discarding state is correct, not destructive.
reset:
	cd infra && rm -f terraform.tfstate terraform.tfstate.backup terraform.tfstate.*.bak && rm -rf .terraform
	@echo ">> State cleared. Rotate creds if needed, then run 'make deploy'."

init:
	$(TF) init -input=false

fmt:
	$(TF) fmt -recursive

validate: init
	$(TF) fmt -check -recursive && $(TF) validate

plan: excel
	$(TF) plan -input=false

apply: whoami excel
	$(TF) apply -auto-approve -input=false

build:
	bash scripts/build_and_push.sh

seed:
	bash scripts/seed.sh

run:
	bash scripts/run_pipeline.sh

deploy: whoami excel apply build seed
	@echo ">> Bring-up complete. Run 'make run' to execute the consistency check."

destroy:
	bash scripts/destroy.sh
