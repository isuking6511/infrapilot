output "ec2_instance_id" {
  value = aws_instance.pilot_ec2.id
}

output "ec2_public_ip" {
  value = aws_instance.pilot_ec2.public_ip
}

output "k3s_role_name" {
  value       = aws_iam_role.k3s_ec2.name
  description = "K3s 노드 IAM Role 이름 (cicd 모듈이 SSM 정책을 붙이는 대상)"
}

output "ssh_command" {
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.pilot_ec2.public_ip}"
  description = "description for ssh command"
}
