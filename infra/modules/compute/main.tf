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

# K3s EC2 IAM Role — 노드가 ECR에서 이미지를 pull(ReadOnly). SSM 관리 권한은 cicd 모듈이 추가.
# (rds:DescribeDBInstances 정책은 Lambda 시절 흔적이라 제거 — 엔드포인트는 K8s Secret으로 주입)
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

  # 주의: user_data를 고치면 AWS가 인스턴스를 stop→start 해서 퍼블릭 IP가 바뀐다.
  # 그래서 살아있는 노드의 user_data는 건드리지 않고, 추가 설정은 Ansible로 한다.
  # (Ubuntu 24.04엔 apt `awscli` 패키지가 없어 아래 설치는 실패함 → Ansible ecr-auth 롤이 snap으로 설치)
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

  # most_recent=true인 data.aws_ami는 Canonical이 새 빌드를 낼 때마다 id가 바뀐다.
  # ami는 변경 불가 속성이라 그대로 두면 plan/apply 때마다 이 살아있는 K3s 노드가
  # destroy→recreate(replace) 대상이 됨 — ignore_changes로 drift를 무시해 방지.
  # AMI를 실제로 올리고 싶을 때는 이 줄을 잠깐 지우고 의도적으로 apply할 것.
  lifecycle {
    ignore_changes = [ami]
  }
}
