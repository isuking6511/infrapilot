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

# 투표/댓글은 재생성 불가한 사용자 데이터라 캐시(Redis) 대신 RDS에 영속.
# RDS는 private subnet + EC2 SG에서만 5432 허용 → 인터넷에서 직접 접근 불가.
module "rds_security" {
  source    = "./modules/rds_security"
  vpc_id    = module.vpc.vpc_id
  ec2_sg_id = module.security.sg_id
}

module "rds" {
  source             = "./modules/rds"
  db_username        = var.db_username
  db_password        = var.db_password
  private_subnet_ids = [module.vpc.private_subnet1_id, module.vpc.private_subnet2_id]
  rds_sg_id          = module.rds_security.sg_id
}

# 웹/스캐너 공용 이미지 레지스트리 (infrapilot-web 하나).
module "ecr" {
  source = "./modules/ecr"
}

module "compute" {
  source    = "./modules/compute"
  subnet_id = module.vpc.public_subnet1_id
  sg_id     = module.security.sg_id
  key_name  = var.key_name
}

module "cicd" {
  source             = "./modules/cicd"
  ecr_repository_arn = module.ecr.repository_arn
  github_repo        = "isuking6511/infrapilot"
}

