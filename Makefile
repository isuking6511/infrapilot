ECR_URL := $(shell cd iac && terraform output -raw ecr_repository_url | tr -d '[:space:]')

build:
	docker build -f Dockerfile.lambda -t $(ECR_URL):latest .

push:
	docker push $(ECR_URL):latest

deploy: build push