#vpc.tf
resource "aws_vpc" "pilot_vpc" {
  cidr_block = var.vpc_cidr
  enable_dns_support = true
  enable_dns_hostnames = true
  tags = {
    Name = "pilot_vpc"
  }
}
#subnet1
resource "aws_subnet" "public_subnet1" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = var.subnet1_cidr
 
  availability_zone = var.az_a

  tags = {
    Name = "public_subnet1"
  }
}
#subnet2
resource "aws_subnet" "public_subnet2" {
  vpc_id = aws_vpc.pilot_vpc.id
  cidr_block = var.subnet2_cidr

  availability_zone = var.az_b

  tags = {
    Name = "public_subnet2"
  }
}
#igw
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.pilot_vpc.id

  tags = {
    Name = "pilot_igw"
  }
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