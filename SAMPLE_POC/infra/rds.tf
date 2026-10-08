# --------------------------------------------------------------------------- #
# Two standard Amazon RDS instances in private subnets.                          #
#   postgres  -> emulates the "Oracle" side    ("system A")                      #
#   mysql     -> emulates the "Snowflake" side ("system B")                     #
#                                                                               #
# NOTE: this is a Pluralsight AWS sandbox. Its Organization SCP explicitly       #
# denies CreateDBInstance for db.serverless (Aurora Serverless v2) but PERMITS   #
# small standard classes, so we use provisioned db.t3.micro RDS instances        #
# instead of Aurora. Single-AZ, no backups, unencrypted storage -> the lightest  #
# footprint the sandbox allows for a throwaway consistency-check POC.            #
# --------------------------------------------------------------------------- #
resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${local.name}-db-subnets" }
}

# ----------------------------- PostgreSQL ---------------------------------- #
resource "aws_db_instance" "pg" {
  identifier     = "${local.name}-pg"
  engine         = "postgres"
  engine_version = var.pg_engine_version
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp2"
  storage_encrypted = false # sandbox KMS is restricted; POC data is throwaway

  db_name  = var.db_name
  username = var.master_username
  password = random_password.pg.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.pg.id]
  multi_az               = false
  publicly_accessible    = false

  backup_retention_period = 0 # disable automated backups (faster create, POC)
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  tags = { Name = "${local.name}-pg" }
}

# ------------------------------- MySQL ------------------------------------- #
resource "aws_db_instance" "mysql" {
  identifier     = "${local.name}-mysql"
  engine         = "mysql"
  engine_version = var.mysql_engine_version
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp2"
  storage_encrypted = false

  db_name  = var.db_name
  username = var.master_username
  password = random_password.mysql.result
  port     = 3306

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.mysql.id]
  multi_az               = false
  publicly_accessible    = false

  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  tags = { Name = "${local.name}-mysql" }
}
