output "ec2_instance_id" {
  value = aws_instance.pilot_ec2.id
}

output "ec2_public_ip" {
  value = aws_instance.pilot_ec2.public_ip
}

output "nat_instance_id" {
  value = aws_instance.nat.id
}

output "nat_instance_eni" {
  value = aws_instance.nat.primary_network_interface_id
}
output "ssh_command" {
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${aws_instance.pilot_ec2.public_ip}"
  description = "description for ssh command"
}
