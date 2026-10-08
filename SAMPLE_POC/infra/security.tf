# Fargate tasks: no inbound; full egress (NAT) to reach ECR/Secrets/SNS/Aurora.
resource "aws_security_group" "fargate" {
  name        = "${local.name}-fargate-sg"
  description = "Fargate task egress"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "all egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-fargate-sg" }
}

# PostgreSQL Aurora: inbound 5432 only from Fargate tasks.
resource "aws_security_group" "pg" {
  name        = "${local.name}-pg-sg"
  description = "Aurora PostgreSQL access from Fargate"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "postgres from fargate"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.fargate.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-pg-sg" }
}

# MySQL Aurora: inbound 3306 only from Fargate tasks.
resource "aws_security_group" "mysql" {
  name        = "${local.name}-mysql-sg"
  description = "Aurora MySQL access from Fargate"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "mysql from fargate"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.fargate.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-mysql-sg" }
}
