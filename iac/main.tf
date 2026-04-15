module "vpc" {
    source = "./modules/vpc"
    vpc_cidr = var.vpc_cidr
    subnet1_cidr = var.subnet1_cidr
    subnet2_cidr = var.subnet2_cidr
    az_a = var.az_a
    az_b = var.az_b
}
module "security" {
    source = "./modules/security"
    my_ip = var.my_ip
    vpc_id = module.vpc.vpc_id
}
module "compute" {
    source = "./modules/compute"
    subnet_id = module.vpc.public_subnet1_id
    sg_id = module.security.sg_id
    key_name = var.key_name
  
}