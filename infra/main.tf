module "vpc" {
  source       = "./modules/vpc"
  vpc_cidr     = var.vpc_cidr
  subnet1_cidr = var.subnet1_cidr
  subnet2_cidr = var.subnet2_cidr
  subnet3_cidr = var.subnet3_cidr
  subnet4_cidr = var.subnet4_cidr
  az_a         = var.az_a
  az_b         = var.az_b
}

module "security" {
  source = "./modules/security"
  my_ip  = var.my_ip
  vpc_id = module.vpc.vpc_id
}

module "nat_security" {
  source   = "./modules/nat_security"
  vpc_id   = module.vpc.vpc_id
  vpc_cidr = var.vpc_cidr
  my_ip    = var.my_ip
}



module "ecr" {
  source = "./modules/ecr"
}

module "compute" {
  source     = "./modules/compute"
  subnet_id  = module.vpc.public_subnet1_id
  sg_id      = module.security.sg_id
  nat_sg_id  = module.nat_security.sg_id
  key_name   = var.key_name
}

# Private subnet → NAT instance 경로 (순환 의존성 방지를 위해 root에서 추가)
resource "aws_route" "private_nat" {
  route_table_id         = module.vpc.private_route_table_id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = module.compute.nat_instance_eni
}



