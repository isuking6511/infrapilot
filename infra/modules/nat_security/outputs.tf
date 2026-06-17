output "sg_id" {
  value       = aws_security_group.nat_sg.id
  description = "NAT Instance Security Group ID"
}
