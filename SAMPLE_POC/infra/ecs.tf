# --------------------------------------------------------------------------- #
# ECS Fargate: one cluster, one task definition (the custom image), four MODEs   #
# driven by env overrides injected by Step Functions / run-task.                #
# --------------------------------------------------------------------------- #
resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled" # keep operational overhead/cost minimal for the POC
  }

  tags = { Name = "${local.name}-cluster" }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.task_family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = local.container_name
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true
      # MODE / RUN_ID / QUERY_ID are injected at runtime via containerOverrides.
      environment = [
        { name = "S3_BUCKET", value = aws_s3_bucket.main.bucket },
        { name = "PG_SECRET_ARN", value = aws_secretsmanager_secret.pg.arn },
        { name = "MYSQL_SECRET_ARN", value = aws_secretsmanager_secret.mysql.arn },
        { name = "SNS_TOPIC_ARN", value = aws_sns_topic.results.arn },
        { name = "EXCEL_KEY", value = "input/queries.xlsx" },
        { name = "ROW_COUNT", value = tostring(var.row_count) },
        { name = "RESULT_DIFF_LIMIT", value = "50" },
        { name = "AWS_DEFAULT_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "app"
        }
      }
    }
  ])

  tags = { Name = local.task_family }
}
