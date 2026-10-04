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

# Configure the AWS Provider
provider "aws" {
  region = "ap-northeast-2"

  # 모든 리소스에 공통 태그 → Cost Explorer에서 Project 태그로 이 프로젝트 비용만 필터링.
  default_tags {
    tags = {
      Project   = "infrapilot"
      ManagedBy = "terraform"
    }
  }
}
