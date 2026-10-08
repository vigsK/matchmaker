output "region" {
  value = var.region
}

output "s3_bucket" {
  description = "Central bucket for source, input Excel, and run artifacts."
  value       = aws_s3_bucket.main.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "codebuild_project" {
  description = "Start a build with: aws codebuild start-build --project-name <this>"
  value       = aws_codebuild_project.image.name
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.app.arn
}

output "task_definition_family" {
  value = aws_ecs_task_definition.app.family
}

output "container_name" {
  value = local.container_name
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "fargate_security_group_id" {
  value = aws_security_group.fargate.id
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.consistency.arn
}

output "pg_secret_arn" {
  value = aws_secretsmanager_secret.pg.arn
}

output "mysql_secret_arn" {
  value = aws_secretsmanager_secret.mysql.arn
}

output "pg_cluster_endpoint" {
  value = aws_db_instance.pg.address
}

output "mysql_cluster_endpoint" {
  value = aws_db_instance.mysql.address
}

output "sns_topic_arn" {
  value = aws_sns_topic.results.arn
}

# Convenience JSON for scripts (network config for `aws ecs run-task`).
output "run_task_network_config" {
  value = jsonencode({
    awsvpcConfiguration = {
      subnets        = aws_subnet.private[*].id
      securityGroups = [aws_security_group.fargate.id]
      assignPublicIp = "DISABLED"
    }
  })
}
