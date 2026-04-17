# Variable definitions for the VPC module
variable "vpc_cidr" {
  type = string
}

#subnet CIDR variables
variable "subnet1_cidr" {
  type = string
}

variable "subnet2_cidr" {
  type = string
}

variable "subnet3_cidr" {
  type = string
}

variable "subnet4_cidr" {
  type = string
}

#AZ variables
variable "az_a" {
  type = string
}

variable "az_b" {
  type = string
}