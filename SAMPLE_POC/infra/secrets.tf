# --------------------------------------------------------------------------- #
# DB credentials in Secrets Manager. The container reads these at runtime via    #
# the task role - nothing sensitive is baked into the image.                     #
# Passwords use a restricted special-char set so they are safe inside SQLAlchemy #
# connection URLs (no @ : / # ? etc.).                                           #
# --------------------------------------------------------------------------- #
resource "random_password" "pg" {
  length           = 24
  special          = true
  override_special = "_-.+="
}

resource "random_password" "mysql" {
  length           = 24
  special          = true
  override_special = "_-.+="
}

resource "aws_secretsmanager_secret" "pg" {
  name                    = "${local.name}/postgres"
  description             = "Aurora PostgreSQL connection details"
  recovery_window_in_days = 0 # POC: delete immediately so re-applies don't clash
}

resource "aws_secretsmanager_secret" "mysql" {
  name                    = "${local.name}/mysql"
  description             = "Aurora MySQL connection details"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "pg" {
  secret_id = aws_secretsmanager_secret.pg.id
  secret_string = jsonencode({
    engine   = "postgres"
    host     = aws_db_instance.pg.address
    port     = 5432
    username = var.master_username
    password = random_password.pg.result
    dbname   = var.db_name
  })
}

resource "aws_secretsmanager_secret_version" "mysql" {
  secret_id = aws_secretsmanager_secret.mysql.id
  secret_string = jsonencode({
    engine   = "mysql"
    host     = aws_db_instance.mysql.address
    port     = 3306
    username = var.master_username
    password = random_password.mysql.result
    dbname   = var.db_name
  })
}
