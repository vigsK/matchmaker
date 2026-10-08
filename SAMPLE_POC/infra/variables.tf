variable "aws_profile" {
  description = "AWS CLI named profile. Set to \"\" to use the default credential chain."
  type        = string
  default     = "sandbox1"
}

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Short name used as a prefix for all resources (lowercase, no spaces)."
  type        = string
  default     = "qmatch"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones (and subnet pairs) to span."
  type        = number
  default     = 2
}

# ---------------------------------------------------------------------------- #
# Standard RDS (PostgreSQL = Oracle side, MySQL = Snowflake side)               #
# Pluralsight sandbox: db.serverless/Aurora is SCP-denied; db.t3.micro is OK.   #
# ---------------------------------------------------------------------------- #
variable "pg_engine_version" {
  description = "RDS PostgreSQL engine version."
  type        = string
  default     = "16.14"
}

variable "mysql_engine_version" {
  description = "RDS MySQL engine version."
  type        = string
  default     = "8.0.46"
}

# Standard RDS instance class. The Pluralsight sandbox SCP denies db.serverless
# (Aurora) but permits small provisioned classes, so default to db.t3.micro.
variable "db_instance_class" {
  description = "RDS instance class for both databases."
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "Allocated storage (GiB) for each RDS instance."
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Initial database/schema name created in both clusters."
  type        = string
  default     = "appdb"
}

variable "master_username" {
  description = "Master username for both clusters."
  type        = string
  default     = "dbadmin"
}

# ---------------------------------------------------------------------------- #
# Workload / Fargate                                                            #
# ---------------------------------------------------------------------------- #
variable "task_cpu" {
  description = "Fargate task CPU units (256/512/1024/...)."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 1024
}

variable "map_max_concurrency" {
  description = "Max parallel query comparisons in the Distributed Map."
  type        = number
  default     = 10
}

variable "row_count" {
  description = "Number of rows seeded into the orders table on each engine."
  type        = number
  default     = 50000
}

variable "image_tag" {
  description = "Container image tag built/pushed to ECR and run by ECS."
  type        = string
  default     = "latest"
}

# ---------------------------------------------------------------------------- #
# Scheduling / notifications                                                    #
# ---------------------------------------------------------------------------- #
variable "schedule_expression" {
  description = "EventBridge Scheduler expression for the daily run (UTC)."
  type        = string
  default     = "cron(0 6 * * ? *)" # 06:00 UTC every day
}

variable "schedule_enabled" {
  description = "Whether the daily schedule is ENABLED."
  type        = bool
  default     = true
}

variable "notification_email" {
  description = "Optional email subscribed to the results SNS topic (\"\" = none)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for ECS/Step Functions/CodeBuild."
  type        = number
  default     = 14
}
