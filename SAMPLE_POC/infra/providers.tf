provider "aws" {
  region = var.region
  # Set to "" to use the default credential chain (env vars / SSO / instance role)
  # so the stack is portable across AWS accounts and CI systems.
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = local.common_tags
  }
}
