AWS_REGION  := ap-northeast-2
ECR_URL     := $(shell cd iac && terraform output -raw ecr_repository_url 2>/dev/null | tr -d '[:space:]')
AWS_ACCOUNT := $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)

# ECR 리포 단독 프로비저닝 (첫 배포 시 이미지 푸시 전에 먼저 실행)
tf-ecr:
	cd iac && terraform apply -target=module.ecr -auto-approve

# Terraform 전체 적용
tf-apply:
	cd iac && terraform apply -auto-approve

# ECR 로그인
ecr-login:
	aws ecr get-login-password --region $(AWS_REGION) | \
	  docker login --username AWS --password-stdin $(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com

build:
	docker build --platform linux/amd64 --provenance=false -f Dockerfile.lambda -t $(ECR_URL):latest .

push: ecr-login
	docker push $(ECR_URL):latest

deploy: build push

# 첫 배포: ECR 생성 → 이미지 빌드/푸시 → 전체 Terraform
full-deploy: tf-ecr deploy tf-apply