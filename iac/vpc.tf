resource "aws_vpc" "pilot_vpc" {
  cidr_block = "10.0.0.0/16"
  enable_dns_support = true
  enable_dns_hostnames = true
  tags = {
    Name = "pilot_vpc"
  }
}

resource "aws_subnet" "public_subnet1" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = "10.0.0.0/24"
 
  availability_zone = "ap-northeast-2a"

  tags = {
    Name = "public_subnet1"
  }
}

resource "aws_subnet" "public_subnet2" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = "10.0.1.0/24"

  availability_zone = "ap-northeast-2b"

  tags = {
    Name = "public_subnet2"
  }
}
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.pilot_vpc.id

  tags = {
    Name = "pilot_igw"
  }
}
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