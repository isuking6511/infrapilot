resource "aws_security_group" "lambda" {
  name        = "infrapilot-lambda-sg"
  description = "Lambda SG"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "infrapilot-lambda-sg"
  }
}