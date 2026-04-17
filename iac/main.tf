module "vpc" {
    source = "./modules/vpc"
    vpc_cidr = var.vpc_cidr
    subnet1_cidr = var.subnet1_cidr
    subnet2_cidr = var.subnet2_cidr
    subnet3_cidr = var.subnet3_cidr
    subnet4_cidr = var.subnet4_cidr
    az_a = var.az_a
    az_b = var.az_b
}
module "security" {
    source = "./modules/security"
    my_ip = var.my_ip
    vpc_id = module.vpc.vpc_id
    
}
module "rds_security" {
    source = "./modules/rds_security"
    vpc_id = module.vpc.vpc_id
    ec2_sg_id = module.security.sg_id
}
module "rds" {
    source = "./modules/rds"
    db_username = var.db_username
    db_password = var.db_password
    private_subnet_ids = [module.vpc.private_subnet1_id, module.vpc.private_subnet2_id]
    rds_sg_id = module.rds_security.sg_id
}
module "compute" {
    source = "./modules/compute"
    subnet_id = module.vpc.public_subnet1_id
    sg_id = module.security.sg_id
    key_name = var.key_name
  
}
module "ecr" {
    source = "./modules/ecr"
}