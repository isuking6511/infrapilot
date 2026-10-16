output "private_ip" {
  value       = aws_instance.monitoring.private_ip
  description = "K3s 노드의 Alloy가 지표·로그를 보낼 사설 IP"
}

output "instance_id" {
  value       = aws_instance.monitoring.id
  description = "SSM 접속용 인스턴스 ID"
}