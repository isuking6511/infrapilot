variable "key_name" {
    type    = string
    default = "infrapilot"
  
}

variable "subnet_id" {
    type = string
}

variable "sg_id" {
    type = string
}
variable "nat_sg_id" {
  type = string
}