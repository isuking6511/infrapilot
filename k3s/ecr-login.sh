#!/bin/bash
# K3s ECR 인증 스크립트
# ECR 토큰은 12시간 유효 → 6시간마다 실행 권장
# crontab: 0 */6 * * * /home/ubuntu/k3s/ecr-login.sh

set -e

REGION="ap-northeast-2"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "ECR 로그인 중: ${ECR_REGISTRY}"

kubectl create secret docker-registry ecr-secret \
  --docker-server="${ECR_REGISTRY}" \
  --docker-username=AWS \
  --docker-password="$(aws ecr get-login-password --region ${REGION})" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "완료"
