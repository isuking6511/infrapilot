# Ubuntu 24.04 LTS arm64 (K3s용)
data "aws_ami" "ubuntu_arm64" {
  most_recent = true
  owners      = ["099720109477"] # Canonical 공식 ID

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Ubuntu 24.04 LTS x86_64 (NAT Instance용)
data "aws_ami" "ubuntu_x86" {
  most_recent = true
  owners      = ["099720109477"] # Canonical 공식 ID

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# K3s EC2가 ECR에서 이미지를 pull하기 위한 IAM Role
resource "aws_iam_role" "k3s_ec2" {
  name = "infrapilot-k3s-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_readonly" {
  role       = aws_iam_role.k3s_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "rds_describe" {
  name = "infrapilot-rds-describe"
  role = aws_iam_role.k3s_ec2.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "rds:DescribeDBInstances"
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "k3s_ec2" {
  name = "infrapilot-k3s-profile"
  role = aws_iam_role.k3s_ec2.name
}

# 메인 EC2 (K3s) - arm64
resource "aws_instance" "pilot_ec2" {
  ami                         = data.aws_ami.ubuntu_arm64.id
  instance_type               = "t4g.micro"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.sg_id]
  key_name                    = var.key_name
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.k3s_ec2.name

  user_data = <<-EOF
#!/bin/bash
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10

# AWS CLI 설치 (ECR 로그인용)
apt-get update -y
apt-get install -y awscli
EOF

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = {
    Name = "infrapilot"
  }
}

# NAT Instance - x86_64, t3.micro
resource "aws_instance" "nat" {
  ami                         = data.aws_ami.ubuntu_x86.id
  instance_type               = "t3.micro"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.nat_sg_id]
  key_name                    = var.key_name
  source_dest_check           = false # NAT 핵심
  associate_public_ip_address = true

  user_data = <<-EOF
#!/bin/bash
exec > /var/log/user-data.log 2>&1
set -x

# IP 포워딩
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p

# iptables (DEBIAN_FRONTEND=noninteractive로 대화형 프롬프트 억제)
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent

# 인터페이스 이름 자동 감지
PRIMARY_IF=$(ip -o -4 route show to default | awk '{print $5}')
iptables -t nat -A POSTROUTING -o "$PRIMARY_IF" -j MASQUERADE

netfilter-persistent save
EOF


  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = {
    Name = "pilot-nat-instance"
  }
}
