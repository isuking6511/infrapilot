#vpc.tf
resource "aws_vpc" "pilot_vpc" {
  cidr_block = var.vpc_cidr
  enable_dns_support = true
  enable_dns_hostnames = true
  tags = {
    Name = "pilot_vpc"
  }
}
# NAT Instance용 AMI (Amazon Linux 2)
data "aws_ami" "nat" {
  most_recent = true
  owners      = ["137112412989"]  # Amazon 공식 ID

  filter {
    name   = "name"
    values = ["al2023-ami-2023*-arm64"]  
  }
}

# NAT Instance on public subnet
resource "aws_instance" "nat" {
  ami                         = data.aws_ami.nat.id
  instance_type               = "t4g.micro"           # Free Tier
  subnet_id                   = aws_subnet.public_subnet1.id
  source_dest_check           = false  # false?
  vpc_security_group_ids      = [aws_security_group.nat_sg.id]
  associate_public_ip_address = true

  tags = {
    Name = "pilot-nat-instance"
  }
}

# NAT Instance 보안그룹
resource "aws_security_group" "nat_sg" {
  name        = "pilot-nat-sg"
  description = "NAT Instance SG"
  vpc_id      = aws_vpc.pilot_vpc.id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.0.0.0/16"]  # VPC 내부에서만 접근
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "pilot-nat-sg"
  }
}

# Private Subnet 라우팅 테이블
resource "aws_route_table" "pilot_private_rt" {
  vpc_id = aws_vpc.pilot_vpc.id

  route {
    cidr_block           = "0.0.0.0/0"
    network_interface_id = aws_instance.nat.primary_network_interface_id
  }

  tags = {
    Name = "pilot_private_rt"
  }
}


#public subnet1
resource "aws_subnet" "public_subnet1" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = var.subnet1_cidr
  map_public_ip_on_launch = true 
  availability_zone = var.az_a

  tags = {
    Name = "public_subnet1"
  }
}
#public subnet2
resource "aws_subnet" "public_subnet2" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = var.subnet2_cidr

  availability_zone = var.az_b

  tags = {
    Name = "public_subnet2"
  }
}
#private subnet1
resource "aws_subnet" "private_subnet1" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = var.subnet3_cidr
 
  availability_zone = var.az_a

  tags = {
    Name = "private_subnet1"
  }
}
#private subnet2
resource "aws_subnet" "private_subnet2" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = var.subnet4_cidr
 
  availability_zone = var.az_b

  tags = {
    Name = "private_subnet2"
  }
}
#igw
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.pilot_vpc.id

  tags = {
    Name = "pilot_igw"
  }
}


# Private Subnet 라우팅 테이블 연결
resource "aws_route_table_association" "private_1" {
  subnet_id      = aws_subnet.private_subnet1.id
  route_table_id = aws_route_table.pilot_private_rt.id
}

resource "aws_route_table_association" "private_2" {
  subnet_id      = aws_subnet.private_subnet2.id
  route_table_id = aws_route_table.pilot_private_rt.id
}
#route table
resource "aws_route_table" "pilot_public_rt" {
  vpc_id = aws_vpc.pilot_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
  tags = {
    Name = "pilot_public_rt"
  }
}
#route table association
resource "aws_route_table_association" "public_1" {
  subnet_id      = aws_subnet.public_subnet1.id
  route_table_id = aws_route_table.pilot_public_rt.id
}
resource "aws_route_table_association" "public_2" {
  subnet_id      = aws_subnet.public_subnet2.id
  route_table_id = aws_route_table.pilot_public_rt.id
}
