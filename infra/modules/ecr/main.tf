# 웹(FastAPI)과 스캐너(CronJob)가 같이 쓰는 이미지 하나만 관리한다.
# 예전 infrapilot-lambda / infrapilot-dashboard 레포는 Lambda·RDS 대시보드 시절 흔적이라 제거.
resource "aws_ecr_repository" "web" {
  name = "infrapilot-web"
  # :latest 태그 갱신용으로 MUTABLE. 실제 배포는 바뀌지 않는 커밋 SHA 태그로 한다.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true # 푸시할 때마다 CVE 기본 스캔 (무료)
  }

  tags = {
    Name = "infrapilot-web"
  }
}

# 이미지 1개 ~100MB 안팎. ECR 무료 스토리지(500MB) 안에 머물도록 최근 3개만 보관.
# 트레이드오프: 4번째 이전 버전으로는 롤백 불가 (rollout undo는 직전 버전이라 충분).
resource "aws_ecr_lifecycle_policy" "web" {
  repository = aws_ecr_repository.web.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 3 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}
