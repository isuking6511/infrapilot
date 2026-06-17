output "ec2_public_ip" {
  value       = module.compute.ec2_public_ip
  description = "ouput from aws for ec2 public ip"
}

output "ec2_instance_id" {
  value       = module.compute.ec2_instance_id
  description = "ouput from aws for ec2 instance id"
}

output "ssh_command" {
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${module.compute.ec2_public_ip}"
  description = "description for ssh command"
}
output "db_endpoint" {
  value       = module.rds.db_endpoint
  description = "RDS 접속 엔드포인트"
}

output "db_name" {
  value = module.rds.db_name
  description = "RDS 데이터베이스 이름"
}
output "ecr_repository_url" {
  value = module.ecr.repository_url
  description = "ecr repository url"
}

output "ecr_dashboard_repository_url" {
  value = module.ecr.dashboard_repository_url
  description = "dashboard ecr repository url"
}