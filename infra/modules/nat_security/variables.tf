variable "vpc_id" {
  type        = string
  description = "VPC ID"
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR (private subnet → NAT 허용 범위)"
}

variable "my_ip" {
  type        = string
  description = "NAT 인스턴스에 접근 허용할 IP"

}
