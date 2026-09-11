# 🚀 InfraPilot

거래소 시세를 수집해 매매 타점 점수를 계산하고, 웹에 랭킹으로 보여주는 분석/시각화 플랫폼.
**자동매매 아님** — 진짜 주인공은 트레이딩 로직이 아니라 클라우드 네이티브 시스템 설계
(이벤트 기반 배치, 데이터 생명주기별 저장소 분리, K3s 오케스트레이션, 하이브리드 클라우드).

## 📅 Daily Progress

- [[daily/2026-04-14]] — Day 1: Terraform VPC & 모듈화
- [[daily/2026-04-15]] — Day 2: Ansible + K3s 설치
- 2026-04-16 ~ 09-11: daily 로그는 CKA 자격증 준비로 중단. 그 기간 실제 진행은 아래
  Phase 로드맵과 커밋 로그(`git log`) 기준으로 재구성함 — 상세 회고는 `blogging/14.md`.

## 🏷️ Phase 로드맵

1. ✅ Phase 1: Terraform VPC & Network
2. ✅ Phase 2: Ansible & K3s Setup
3. ✅ Phase 3: `analysis_core` 순수 계산 엔진 (models→pivots→ict→neely→confluence→setup→risk→scanner,
   Pine→Python 골든 테스트로 검증)
4. ✅ Phase 4: 수집·배치·서빙 — K3s CronJob(`scanner_job`, 봉마감 배치) → Redis 랭킹 캐시 →
   FastAPI 웹 대시보드(Lightweight Charts). 초기 Lambda+RDS 설계에서 전환(재생성 가능한
   분석 결과 = 캐시가 맞다는 판단, 저장소 교체해도 API 인터페이스 불변)
5. 🔄 Phase 5: 인프라 정리 — Lambda/RDS Terraform 모듈 제거, K3s 배포 안정화(ECR pull
   인증 이슈 우회), Ansible 보안 하드닝(sshd/fail2ban 핸들러)
6. ⬜ Phase 6: Monitoring (Prometheus + Grafana)
7. ⬜ Phase 7: 투표/댓글(재생성 불가 데이터 → RDS 재도입) + CI/CD
8. ⬜ Phase 8: 코스피/나스닥 데이터소스, GCP 배치(백테스트) + Tailscale 연결


