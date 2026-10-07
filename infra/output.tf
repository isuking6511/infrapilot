output "ec2_public_ip" {
  value       = module.compute.ec2_public_ip
  description = "K3s 노드 퍼블릭 IP (대시보드: http://<ip>:30080)"
}

output "ec2_instance_id" {
  value       = module.compute.ec2_instance_id
  description = "K3s 노드 인스턴스 ID"
}

output "ssh_command" {
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${module.compute.ec2_public_ip}"
  description = "SSH 접속 명령 (SG에서 my_ip만 허용)"
}

output "web_repository_url" {
  value       = module.ecr.repository_url
  description = "웹/스캐너 이미지 ECR URL (k3s/*.yaml image에 사용)"
}

# output "deploy_role_arn" {
#   value       = module.cicd.deploy_role_arn
#   description = "GitHub 저장소 변수 AWS_DEPLOY_ROLE_ARN에 넣을 값"
# }

output "db_endpoint" {
  value       = module.rds.db_endpoint
  description = "RDS 접속 엔드포인트 (votes/comments)"
}

output "db_name" {
  value       = module.rds.db_name
  description = "RDS 데이터베이스 이름"
}

output "github_actions_role_arn" {
  value       = module.cicd.role_arn
  description = "GitHub Variables의 AWS_ROLE_ARN에 넣을 값"
}
