resource "aws_instance" "pilot_ec2" {
  ami                         = "ami-084a56dceed3eb9bb"   # Ubuntu 24.04 
  instance_type               = "t3.micro"           # Free Tier
  subnet_id                   =  var.subnet_id        
  vpc_security_group_ids      = [var.sg_id]          
  key_name                    = var.key_name            # SSH 키페어
  associate_public_ip_address = true             # 퍼블릭 IP 필요
  
  # 스왑 메모리 설정

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
    volume_size = 20   # GB 단위, Free Tier 30GB까지 무료
    volume_type = "gp3"
  }

  tags = {
    Name = "infrapilot"
  }
}