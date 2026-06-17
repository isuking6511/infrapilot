output "vpc_id" {
  value       = aws_vpc.pilot_vpc.id
  description = "ID of the VPC."
}

output "public_subnet1_id" {
  value       = aws_subnet.public_subnet1.id
  description = "ID of the first public subnet."
}

output "public_subnet2_id" {
  value       = aws_subnet.public_subnet2.id
  description = "ID of the second public subnet."
}
output "private_subnet1_id" {
  value       = aws_subnet.private_subnet1.id
  description = "ID of the first private subnet."
}

output "private_subnet2_id" {
  value       = aws_subnet.private_subnet2.id
  description = "ID of the second private subnet."
}

output "public_subnet_ids" {
  value       = [aws_subnet.public_subnet1.id, aws_subnet.public_subnet2.id]
  description = "IDs of all public subnets."
}

output "private_subnet_ids" {
  value       = [aws_subnet.private_subnet1.id, aws_subnet.private_subnet2.id]
  description = "IDs of all private subnets."
}

output "igw_id" {
  value       = aws_internet_gateway.gw.id
  description = "ID of the Internet Gateway."
}

output "public_route_table_id" {
  value       = aws_route_table.pilot_public_rt.id
  description = "ID of the public route table."
}

output "private_route_table_id" {
  value       = aws_route_table.pilot_private_rt.id
  description = "ID of the private route table."
}
