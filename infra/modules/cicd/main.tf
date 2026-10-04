# GitHub Actions → AWS 배포 권한 (OIDC) + EC2의 SSM 관리 권한
#
# 왜 OIDC인가: Access Key를 GitHub Secrets에 넣으면 만료가 없고 유출 시 피해가 큼.
# OIDC는 워크플로 실행마다 15분~1시간짜리 임시 자격증명을 받고,
# 신뢰 정책으로 "이 저장소의 main 브랜치"만 Role을 쓸 수 있게 제한할 수 있음.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}

# ── 1. GitHub OIDC Provider (계정당 1개) ─────────────────────────────
# 이미 계정에 만들어져 있다면 이 리소스 대신 data "aws_iam_openid_connect_provider" 로 참조할 것.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# ── 2. 배포 Role: 지정 저장소의 main 브랜치에서만 Assume 가능 ─────────
resource "aws_iam_role" "github_deploy" {
  name                 = "infrapilot-github-deploy"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

# ── 3. 최소 권한 정책 ────────────────────────────────────────────────
resource "aws_iam_role_policy" "github_deploy" {
  name = "infrapilot-github-deploy"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken은 리소스 수준 제한을 지원하지 않는 API (AWS 제약)
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "EcrPushWebOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = var.ecr_repository_arn
      },
      {
        # 대상 EC2 조회 (Describe 계열은 리소스 제한 불가)
        Sid      = "FindInstance"
        Effect   = "Allow"
        Action   = "ec2:DescribeInstances"
        Resource = "*"
      },
      {
        # SSM 명령은 RunShellScript 문서 + Name 태그가 일치하는 인스턴스에만
        Sid      = "SsmSendCommandDocument"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ssm:${local.region}::document/AWS-RunShellScript"
      },
      {
        Sid      = "SsmSendCommandTaggedInstance"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = { "ssm:resourceTag/Name" = var.instance_name_tag }
        }
      },
      {
        Sid      = "SsmReadResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*"
      },
    ]
  })
}

# ── 4. 기존 K3s EC2 Role에 SSM 관리 권한 추가 ────────────────────────
# Ubuntu 24.04 공식 AMI에는 SSM Agent가 기본 설치돼 있어 Role만 붙이면 됨.
resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = var.ec2_role_name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
