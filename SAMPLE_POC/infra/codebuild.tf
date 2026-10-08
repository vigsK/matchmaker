# --------------------------------------------------------------------------- #
# CodeBuild: pulls the Python base image, installs dependencies, builds the      #
# custom image, and pushes it to ECR. Source is the zip uploaded to S3.          #
# --------------------------------------------------------------------------- #
resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/codebuild/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_codebuild_project" "image" {
  name          = "${local.name}-image-build"
  description   = "Build and push the query-matcher container image to ECR"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 30

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true # required to build Docker images

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = local.account_id
    }
    environment_variable {
      name  = "IMAGE_REPO_NAME"
      value = aws_ecr_repository.app.name
    }
    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.main.bucket}/${aws_s3_object.source_zip.key}"
    buildspec = "buildspec.yml"
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.codebuild.name
    }
  }

  tags = { Name = "${local.name}-image-build" }
}
