resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "consistency" {
  name     = local.state_machine_name
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/statemachine.asl.json", {
    bucket          = aws_s3_bucket.main.bucket
    cluster_arn     = aws_ecs_cluster.main.arn
    task_def_arn    = aws_ecs_task_definition.app.arn
    container_name  = local.container_name
    subnets_json    = jsonencode(aws_subnet.private[*].id)
    security_group  = aws_security_group.fargate.id
    max_concurrency = var.map_max_concurrency
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = { Name = local.state_machine_name }

  depends_on = [aws_iam_role_policy.sfn]
}
