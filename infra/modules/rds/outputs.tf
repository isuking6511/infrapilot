output "db_endpoint" {
  value       = split(":", aws_db_instance.main.endpoint)[0]
  description = "RDS 접속 엔드포인트 (포트 제외)"
}
output "db_name" {
  value = aws_db_instance.main.db_name
  description = "RDS 데이터베이스 이름"
}
