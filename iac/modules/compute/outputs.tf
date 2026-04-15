output "ec2_public_ip" {
  value       = aws_instance.pilot_ec2.public_ip
  description = "ouput from aws for ec2 public ip"
}

output "ec2_instance_id" {
  value       = aws_instance.pilot_ec2.id
  description = "ouput from aws for ec2 instance id"
}

output "ssh_command" {
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.pilot_ec2.public_ip}"
  description = "description for ssh command"
}