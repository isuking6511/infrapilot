#vpc.tf
resource "aws_vpc" "pilot_vpc" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "pilot_vpc"
  }
}

# Public Subnet 1
resource "aws_subnet" "public_subnet1" {
  vpc_id                  = aws_vpc.pilot_vpc.id
  cidr_block              = var.subnet1_cidr
  map_public_ip_on_launch = true
  availability_zone       = var.az_a

  tags = {
    Name = "public_subnet1"
  }
}

# Public Subnet 2
resource "aws_subnet" "public_subnet2" {
  vpc_id            = aws_vpc.pilot_vpc.id
  cidr_block        = var.subnet2_cidr
  availability_zone = var.az_b

  tags = {
    Name = "public_subnet2"
  }
}

# Private Subnet 1
resource "aws_subnet" "private_subnet1" {
  vpc_id            = aws_vpc.pilot_vpc.id
  cidr_block        = var.subnet3_cidr
  availability_zone = var.az_a

  tags = {
    Name = "private_subnet1"
  }
}

# Private Subnet 2
resource "aws_subnet" "private_subnet2" {
  vpc_id            = aws_vpc.pilot_vpc.id
  cidr_block        = var.subnet4_cidr
  availability_zone = var.az_b

  tags = {
    Name = "private_subnet2"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.pilot_vpc.id

  tags = {
    Name = "pilot_igw"
  }
}

# Public Route Table
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

resource "aws_route_table_association" "public_1" {
  subnet_id      = aws_subnet.public_subnet1.id
  route_table_id = aws_route_table.pilot_public_rt.id
}

resource "aws_route_table_association" "public_2" {
  subnet_id      = aws_subnet.public_subnet2.id
  route_table_id = aws_route_table.pilot_public_rt.id
}

# Private Route Table (NAT 경로는 root에서 aws_route로 추가)
resource "aws_route_table" "pilot_private_rt" {
  vpc_id = aws_vpc.pilot_vpc.id

  tags = {
    Name = "pilot_private_rt"
  }
}

resource "aws_route_table_association" "private_1" {
  subnet_id      = aws_subnet.private_subnet1.id
  route_table_id = aws_route_table.pilot_private_rt.id
}

resource "aws_route_table_association" "private_2" {
  subnet_id      = aws_subnet.private_subnet2.id
  route_table_id = aws_route_table.pilot_private_rt.id
}
