output "db_endpoint" {
  value       = aws_db_instance.main.endpoint
  description = "RDS 접속 엔드포인트"
}

output "db_name" {
  value = aws_db_instance.main.db_name
  description = "RDS 데이터베이스 이름"
}
