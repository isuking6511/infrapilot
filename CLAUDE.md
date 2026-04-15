# InfraPilot

## 프로젝트 개요
AI Multi-Agent Trading & Self-Healing Infrastructure
K3s 위에서 AI 에이전트 팀이 자동매매 + 인프라 자율운영

## 기술 스택
- IaC: Terraform (AWS ap-northeast-2)
- Config: Ansible
- Container: K3s
- Language: Python 3.11
- AI: Provider-agnostic (Claude/OpenAI/Gemini/Ollama)

## 규칙
- 코드를 바로 작성하지 말 것. 목표와 힌트만 제시
- 내가 직접 작성하고, 막히면 도와줘
- 면접에서 설명할 수 있어야 하니까 "왜"를 항상 설명해줘
- Terraform은 iac/ 폴더
- Python은 src/infrapilot/ 폴더

## 현재 진행
- Phase 1: Terraform VPC & Network (진행 중)
## 보안
- ~/.aws/ 폴더 절대 읽지 말 것
- API 키, 시크릿, 토큰 출력하지 말 것
- .env 파일 내용 출력하지 말 것