# Output variables for the security group module
output "sg_id" {
  value       = aws_security_group.pilot_rds_sg.id
  description = "ID of the RDS security group."
}
