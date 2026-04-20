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

module "lambda_sg" {
  source = "./modules/lambda_security"
  vpc_id = module.vpc.vpc_id
}

module "rds_security" {
  source       = "./modules/rds_security"
  vpc_id       = module.vpc.vpc_id
  ec2_sg_id    = module.security.sg_id
  lambda_sg_id = module.lambda_sg.sg_id
}

module "rds" {
  source             = "./modules/rds"
  db_username        = var.db_username
  db_password        = var.db_password
  private_subnet_ids = [module.vpc.private_subnet1_id, module.vpc.private_subnet2_id]
  rds_sg_id          = module.rds_security.sg_id
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

module "lambda" {
  source         = "./modules/lambda"
  vpc_id         = module.vpc.vpc_id
  subnet_ids     = [module.vpc.private_subnet1_id, module.vpc.private_subnet2_id]
  ecr_image_uri  = "${module.ecr.repository_url}:latest"
  db_host        = module.rds.db_endpoint
  db_password    = var.db_password
  lambda_sg_id   = module.lambda_sg.sg_id
  gemini_api_key = var.gemini_api_key
}

