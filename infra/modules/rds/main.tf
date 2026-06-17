resource "aws_db_subnet_group" "main" {
  name       = "infrapilot-db-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "infrapilot-db-subnet-group"
  }
}

resource "aws_db_instance" "main" {
  identifier        = "infrapilot-db"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.t3.micro"
  allocated_storage = 20

  db_name  = "infrapilot"
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.rds_sg_id]

  publicly_accessible = false
  skip_final_snapshot = true

  tags = {
    Name = "infrapilot-db"
  }
}