resource "aws_security_group" "pilot_sg" {
  name        = "pilot_sg"
  description = "infrapilot sg"
  vpc_id      = var.vpc_id

  tags = {
    Name = "pilot_sg"
  }
}
resource "aws_vpc_security_group_ingress_rule" "allow_ssh" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = var.my_ip
  from_port         = 22
  ip_protocol       = "tcp"
  to_port           = 22
}
resource "aws_vpc_security_group_ingress_rule" "allow_k8s" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = var.my_ip
  from_port         = 6443
  ip_protocol       = "tcp"
  to_port           = 6443
}
resource "aws_vpc_security_group_ingress_rule" "allow_http" {
  security_group_id = aws_security_group.pilot_sg.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP from CloudFront only"
}


resource "aws_vpc_security_group_ingress_rule" "allow_dashboard" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 30080
  ip_protocol       = "tcp"
  to_port           = 30080
}

resource "aws_vpc_security_group_egress_rule" "allow_all_outbound" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1" # semantically equivalent to all ports
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}