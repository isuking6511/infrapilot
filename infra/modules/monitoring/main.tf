data "aws_ami" "ubuntu_arm64" {
  most_recent = true
  owners      = ["099720109477"] # Canonical 공식

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
}

# kuber에서만 
resource "aws_security_group" "monitoring" {
  name        = "monitoring_sg"
  description = "monitoring node - ingress only from k3s node sg"
  vpc_id      = var.vpc_id
  tags        = { Name = "monitoring_sg" }
}

# K3s 노드의 Alloy → VictoriaMetrics 
resource "aws_vpc_security_group_ingress_rule" "metrics_from_k3s" {
  security_group_id            = aws_security_group.monitoring.id
  referenced_security_group_id = var.k3s_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 8428
  to_port                      = 8428
}

# K3s 노드의 Alloy → Loki 
resource "aws_vpc_security_group_ingress_rule" "logs_from_k3s" {
  security_group_id            = aws_security_group.monitoring.id
  referenced_security_group_id = var.k3s_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 3100
  to_port                      = 3100
}

# 아웃바운드
resource "aws_vpc_security_group_egress_rule" "all_outbound" {
  security_group_id = aws_security_group.monitoring.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ── IAM: SSM 접속만 허용 ──
resource "aws_iam_role" "monitoring" {
  name = "infrapilot-monitoring-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "monitoring" {
  name = "infrapilot-monitoring-profile"
  role = aws_iam_role.monitoring.name
}

# ── EC2: t4g.micro (1GB) + swap 1GB ──
resource "aws_instance" "monitoring" {
  ami                         = data.aws_ami.ubuntu_arm64.id
  instance_type               = "t4g.micro"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.monitoring.id]
  key_name                    = var.key_name # SSH는 22번을 열지 않고 SSM 터널로만
  associate_public_ip_address = true         # NAT 없음 → 이미지 pull·SSM 통신용(인바운드는 SG가 막음)
  iam_instance_profile        = aws_iam_instance_profile.monitoring.name

  user_data = <<-EOF
    #!/bin/bash
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl vm.swappiness=10
  EOF

  root_block_device {
    volume_size = 20 # 지표·로그 보관용
    volume_type = "gp3"
  }

  lifecycle {
    ignore_changes = [ami] # 새 AMI가 나와도 교체(replace)되지 않게
  }

  tags = { Name = "infrapilot-monitoring" }
}