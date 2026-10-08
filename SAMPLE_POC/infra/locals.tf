data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = var.region
  name       = var.project

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Deterministic, globally-unique bucket name (reproducible per account/region).
  bucket_name = "${var.project}-${local.account_id}-${var.region}"

  ecr_repo_name         = "${var.project}-app"
  container_name        = "app"
  task_family           = "${var.project}-task"
  task_def_arn_wildcard = "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${var.project}-task:*"

  # State-machine ARN is computed from its (known) name to avoid a dependency
  # cycle between the IAM role policy and the state machine resource.
  state_machine_name = "${var.project}-consistency"
  state_machine_arn  = "arn:aws:states:${var.region}:${local.account_id}:stateMachine:${local.state_machine_name}"

  common_tags = {
    Project   = var.project
    ManagedBy = "terraform"
    Component = "query-consistency-poc"
  }
}
