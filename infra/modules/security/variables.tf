# 유동 IP 변수

variable "my_ip" {
  type    = string
  default = "0.0.0.0/0"
}
variable "vpc_id" {
  type = string
}