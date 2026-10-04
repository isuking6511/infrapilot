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

# GitHub Actions → AWS 배포 권한(OIDC, 장기 키 0개) + EC2에 SSM 관리 권한.
# ECR ARN·EC2 Role 이름을 output으로 받아 의존 관계를 명시적으로 연결한다.
# module "cicd" {
#   source             = "./modules/cicd"
#   ecr_repository_arn = module.ecr.repository_arn
#   ec2_role_name      = module.compute.k3s_role_name
# }

# [제거됨] NAT Instance + nat_security + private route
# 왜: NAT는 private subnet의 Lambda가 Bybit에 나가기 위해 있었는데, Lambda를 K3s CronJob으로
# 옮긴 뒤 private subnet에는 RDS만 남았다. RDS는 아웃바운드 인터넷이 필요 없으므로 NAT는
# 하는 일 없이 월 ~$14(t3.micro + 공인 IPv4 + EBS)만 쓰고 있었다 → 제거.
# private route table은 local 경로만 남아 "인터넷으로 나갈 길 자체가 없는" 서브넷이 된다.
