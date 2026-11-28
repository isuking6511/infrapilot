terraform {
  required_version = ">= 1.10"
  backend "s3" {
    bucket       = "tfstate-1004-477537078211-ap-northeast-2-an"
    key          = "infrapilot/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }





  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"


  default_tags {
    tags = {
      Project   = "infrapilot"
      ManagedBy = "terraform"
    }
  }
}
