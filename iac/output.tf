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