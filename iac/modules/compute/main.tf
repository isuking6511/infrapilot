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

# 메인 EC2 (K3s) - arm64
resource "aws_instance" "pilot_ec2" {
  ami                         = data.aws_ami.ubuntu_arm64.id
  instance_type               = "t4g.micro"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.sg_id]
  key_name                    = var.key_name
  associate_public_ip_address = true

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
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p
apt-get update
apt-get install -y iptables-persistent
iptables -t nat -A POSTROUTING -o ens5 -j MASQUERADE
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
