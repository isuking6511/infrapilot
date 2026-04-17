resource "aws_security_group" "pilot_rds_sg" {
  name        = "pilot_rds_sg"
  description = "infrapilot rds sg"
  vpc_id      = var.vpc_id

  tags = {
    Name = "pilot_rds_sg"
  }
}
# 1. EC2 to RDS
resource "aws_vpc_security_group_ingress_rule" "allow_ec2_to_postgres" {
  security_group_id            = aws_security_group.pilot_rds_sg.id # RDS 보안 그룹 ID
  referenced_security_group_id = var.ec2_sg_id
  from_port                    = 5432
  ip_protocol                  = "tcp"
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "rds_outbound" {
  security_group_id = aws_security_group.pilot_rds_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
