# 🚀 InfraPilot Vault

AI Multi-Agent Trading & Self-Healing Infrastructure 프로젝트 문서.

## 📅 Daily Progress

- [[daily/2026-04-14]] — Day 1: Terraform VPC & 모듈화
- [[daily/2026-04-15]] — Day 2: Ansible + K3s 설치
- [[daily/2026-04-16]] - Day 3: Python Logic
- [[daily/2026-04-16]] - Day 4: RDS MODULE 

## 🏷️ Phase 로드맵

1. ✅ Phase 1: Terraform VPC & Network (완료)
2. ✅ Phase 2: Ansible & K3s Setup (완료)
3. 🔄 Phase 3: Python AI Agent Team
4. ⬜ Phase 4: Monitoring (Prometheus + Grafana)
5. ⬜ Phase 5: CI/CD + PyPI 배포
6. ⬜ Phase 6: Docs & 오픈소스 릴리즈


Lambda Terraform 모듈
  └── modules/lambda/ 작성
  └── modules/rds_security에 lambda_sg 추가
  └── EventBridge 5분 스케줄
  └── terraform apply
  └── Lambda 콘솔에서 테스트 실행 확인


FastAPI 대시보드
  └── RDS에서 OHLCV 조회 API
  └── plotly로 캔들차트 생성
  └── 심볼별 차트 웹페이지
  └── AI 분석 결과 표시

  └── FastAPI Docker 이미지 빌드
  └── K3s 매니페스트 작성
  └── EC2에 배포


  └── GitHub Actions
  └── 코드 push → ECR 이미지 빌드 → Lambda 업데이트
  └── FastAPI 이미지 빌드 → K3s 배포


  └── Prometheus + Grafana
  └── Lambda 실행 횟수, 에러율
  └── RDS 연결 수, 쿼리 시간


  └── Route 53 or 가비아
  └── EC2 IP 연결
  └── HTTPS 설정