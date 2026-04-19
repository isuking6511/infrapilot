resource "aws_security_group" "nat_sg" {
  name        = "pilot-nat-sg"
  description = "NAT Instance Security Group"
  vpc_id      = var.vpc_id

  tags = {
    Name = "pilot-nat-sg"
  }
}

# VPC 내부에서 오는 모든 트래픽 허용 (private subnet → NAT)
resource "aws_vpc_security_group_ingress_rule" "nat_from_vpc" {
  security_group_id = aws_security_group.nat_sg.id
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "-1"
}

# 인터넷으로 모든 아웃바운드 허용
resource "aws_vpc_security_group_egress_rule" "nat_outbound" {
  security_group_id = aws_security_group.nat_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
