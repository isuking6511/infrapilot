output "sg_id" {
  value       = aws_security_group.lambda.id
  description = "Lambda SG ID (rds_security 모듈에서 참조)"
}