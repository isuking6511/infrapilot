# InfraPilot 운영 런북

배포·첫 설정·검증·철수 절차. 위에서부터 순서대로 하면 됩니다.
명령 앞의 `[mac]`은 내 컴퓨터, `[node]`는 EC2(K3s 노드)에서 실행.
노드 접속: `ssh -i ~/.ssh/infrapilot.pem ubuntu@<ec2_public_ip>` 또는 `aws ssm start-session --target <instance-id>`.

---

## 0. 사전 준비 (5분)

`[mac]` `infra/terraform.tfvars`에 내 IP 추가 — 이제 기본값이 없어서 안 넣으면 plan이 멈춥니다.
```hcl
my_ip = "x.x.x.x/32"   # curl -s https://checkip.amazonaws.com
```
IP가 바뀌면(카페·집) 이 값만 고치고 `terraform apply` 하면 SG 규칙만 바뀝니다. 배포는 SSM이라 영향 없음.

## 1. apply 전 정리 (10분)

이번 변경은 옛 리소스를 지우고 새 리소스를 만듭니다. 그냥 apply하면 걸리는 것들을 먼저 처리합니다.

```bash
# [mac] (a) 옛 ECR 레포는 이미지가 남아 있으면 Terraform이 못 지움(RepositoryNotEmpty) → 미리 강제 삭제
aws ecr delete-repository --repository-name infrapilot-lambda    --force --region ap-northeast-2
aws ecr delete-repository --repository-name infrapilot-dashboard --force --region ap-northeast-2

# (b) infrapilot-web 레포를 예전에 콘솔에서 만들었다면 Terraform 관리로 가져오기
aws ecr describe-repositories --repository-names infrapilot-web --region ap-northeast-2 \
  && terraform -chdir=infra import module.ecr.aws_ecr_repository.web infrapilot-web

# (c) 계정에 GitHub OIDC Provider가 이미 있다면 가져오기 (없으면 에러 → 무시하고 진행)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
terraform -chdir=infra import module.cicd.aws_iam_openid_connect_provider.github \
  arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com
```

```bash
# [node] 옛 대시보드 Deployment가 남아 있으면 정리 (k3s/dashboard.yaml 시절)
k3s kubectl delete deploy,svc infrapilot-dashboard --ignore-not-found
```

## 2. Terraform plan → apply (20분, RDS 생성에 5~10분)

```bash
[mac] terraform -chdir=infra init
[mac] terraform -chdir=infra plan
```

**plan에서 확인할 것** — 이것 말고 다른 게 보이면 멈추고 확인:

| 동작 | 리소스 |
|---|---|
| `-` destroy | `module.compute.aws_instance.nat`, `module.nat_security.*`, `aws_route.private_nat`, `module.compute.aws_iam_role_policy.rds_describe` |
| `+` create | `module.rds.*`(DB subnet group·인스턴스), `module.rds_security.*`, `module.ecr.aws_ecr_repository.web`·lifecycle, `module.cicd.*`(OIDC·배포 Role·정책·SSM 연결) |
| `~` / `-/+` | SG 규칙 `allow_ssh`·`allow_k8s`(my_ip로 좁혀짐), 리소스 태그(default_tags) |

> ★ `module.compute.aws_instance.pilot_ec2`에 `-/+`(replace)가 뜨면 **절대 apply 하지 말 것.** 태그 `~` 변경만 정상.

```bash
[mac] terraform -chdir=infra apply
[mac] terraform -chdir=infra output    # web_repository_url, deploy_role_arn, db_endpoint 메모
```

## 3. Ansible (10분)

```bash
[mac] cd infra/ansible
[mac] cp inventory/dev.example.yaml inventory/dev.yaml     # ansible_host = ec2_public_ip
[mac] ansible-galaxy collection install community.general ansible.posix
[mac] ansible-playbook site.yaml
```
이번에 추가로 하는 일:
- `ecr-auth` 롤: aws-cli(snap) 설치, `ecr-refresh.timer`(6시간마다 `ecr-secret` 갱신) 등록·1회 실행, 옛 `ecr-login.sh` crontab 제거
- `k3s-install` 롤: `/etc/rancher/k3s/config.yaml`로 Traefik 비활성화 → k3s 재시작 (실행 중인 Pod는 유지)

```bash
[node] systemctl list-timers ecr-refresh.timer      # NEXT가 보이면 OK
[node] k3s kubectl get secret ecr-secret            # 존재 확인
[node] k3s kubectl get pods -A | grep traefik       # 아무것도 안 나오면 OK
```

## 4. SSM 연결 확인 (5분)

```bash
[mac] aws ssm describe-instance-information \
  --query 'InstanceInformationList[].[InstanceId,PingStatus]' --output table
```
`Online`이면 OK. 방금 Role을 붙였다면 몇 분 걸림. 10분 지나도 없으면 `[node] sudo snap restart amazon-ssm-agent`.

## 5. DB Secret 생성 (5분)

```bash
[node] k3s kubectl create secret generic db-secret \
  --from-literal=host=<terraform output db_endpoint> \
  --from-literal=name=infrapilot \
  --from-literal=user=<tfvars의 db_username> \
  --from-literal=password='<tfvars의 db_password>' \
  --from-literal=vote-salt="$(openssl rand -hex 16)"
```
테이블은 웹이 첫 DB 접속 때 자동 생성합니다(`CREATE TABLE IF NOT EXISTS`).

## 6. GitHub 설정 (5분)

저장소 → Settings → Secrets and variables → Actions → **Variables** 탭 (Secrets 아님 — 둘 다 비밀값이 아님):
- `AWS_DEPLOY_ROLE_ARN` = `terraform output -raw deploy_role_arn`
- `DEPLOY_ENABLED` = `true`

> deploy 잡은 `ubuntu-24.04-arm` 러너를 씁니다(공개 저장소 무료). 저장소가 비공개라 이 러너가 안 잡히면
> `.github/workflows/ci-cd.yml`의 `runs-on`을 `ubuntu-latest`로 바꾸고 `docker/setup-qemu-action@v3` 스텝을 추가.

## 7. 커밋 & 첫 배포

```bash
[mac] git rm --cached infra/ansible/inventory/dev.yaml   # 퍼블릭 IP가 레포에 올라가 있던 것 제거
[mac] make check                                          # ruff + pytest + terraform fmt -check
[mac] git status                                          # 올라갈 파일 확인 (_to_delete/는 .gitignore 대상)
[mac] git add -A
[mac] git commit -m "feat: CI/CD(OIDC·SSM·자동 롤백) + 인프라 정리(NAT·Lambda 흔적 제거) + 관측성"
[mac] git push
```
push하면 CI가 돌고, 앱 파일이 바뀌었으니 deploy까지 이어집니다.
(이미 push했거나 강제로 다시 배포하려면 Actions → CI/CD → **Run workflow**)

## 8. 검증

```bash
[node] k3s kubectl get pods -o wide
[node] k3s kubectl get deploy infrapilot-web -o jsonpath='{.spec.template.spec.containers[0].image}'   # 커밋 SHA 태그
[node] k3s kubectl create job --from=cronjob/infrapilot-scanner-job scan-now   # 15분 안 기다리고 바로 스캔
[node] k3s kubectl logs -f job/scan-now
[mac]  curl -s http://<ec2_public_ip>:30080/health
[mac]  curl -s http://<ec2_public_ip>:30080/metrics | grep infrapilot_scanner
```
브라우저에서 `http://<ec2_public_ip>:30080` → 종목 클릭 → 투표·댓글 남기기.
다른 기기(휴대폰 LTE)에서도 투표해 보고 집계가 2표로 늘면 클라이언트 IP 보존(`externalTrafficPolicy: Local`)도 확인된 것.

## 9. (선택) 모니터링

1. Grafana Cloud 무료 가입 → Connections → Prometheus → remote_write URL·username·토큰 확인
2. `[node]` `k3s kubectl create secret generic grafana-cloud --from-literal=url=... --from-literal=username=... --from-literal=password=...`
3. `[node]` `k3s kubectl apply -f alloy.yaml` (레포의 `k3s/monitoring/alloy.yaml`을 노드에 복사)
4. Grafana에서 패널·알람 (PromQL):
   - 스캐너 정지: `time() - infrapilot_scanner_last_success_timestamp_seconds > 1800`
   - 5xx 비율: `sum(rate(infrapilot_http_requests_total{status=~"5.."}[5m])) / sum(rate(infrapilot_http_requests_total[5m]))`
   - p95 지연: `histogram_quantile(0.95, sum by (le) (rate(infrapilot_http_request_duration_seconds_bucket[5m])))`
   - 셋업 수: `infrapilot_scanner_setups`

메모리가 빠듯하면(`kubectl top pods`) Alloy는 빼도 됩니다. `/metrics`는 그대로 남아 있음.

## 10. 포트폴리오 증거 캡처 ★ (프리 플랜 종료 전)

- [ ] Actions 화면: CI 잡 6개 + Deploy 초록불
- [ ] Deploy 로그의 SSM stdout (rollout 성공 + `get pods`)
- [ ] **롤백 시연**: `dashboard/main.py`의 `/health`가 500을 반환하게 바꿔 push → readiness 실패 → `rollout undo` 로그 캡처 → 원복 push
- [ ] 대시보드 화면 (랭킹·차트 진입/손절/목표선·투표·댓글)
- [ ] `/metrics` 또는 Grafana 화면
- [ ] `terraform plan`이 "No changes"인 화면 (코드 = 실제 인프라)

캡처는 `docs/evidence/`에 넣고 README "배포 증거"에 링크.

## 11. 철수 (프리 플랜 종료 시)

```bash
# 1) GitHub 변수 DEPLOY_ENABLED = false   → 이후 CI는 계속 초록불, deploy만 건너뜀
# 2) (원하면) RDS 데이터 보관
[node] k3s kubectl run pgdump --rm -it --image=postgres:15 --restart=Never -- \
  pg_dump "postgresql://<user>:<pw>@<db_endpoint>/infrapilot" > votes_backup.sql
# 3) 전부 삭제
[mac] terraform -chdir=infra destroy
# 4) 남는 과금 확인: EC2·RDS·EBS 볼륨·Elastic IP·ECR이 0인지, Billing 콘솔 확인
```
다시 올릴 땐 0~8단계를 반복하면 됩니다(IaC라 `apply` 한 번 + 플레이북 한 번).

---

## 자주 보는 장애

| 증상 | 확인 | 조치 |
|---|---|---|
| `ImagePullBackOff` | `kubectl describe pod` → `no basic auth`/`401` | `[node] sudo systemctl start ecr-refresh.service` 후 Pod 삭제 |
| 웹은 뜨는데 "rankings 아직 없음" | `kubectl get cronjob,jobs` | `kubectl create job --from=cronjob/infrapilot-scanner-job scan-now` |
| 투표/댓글만 503 | `kubectl logs deploy/infrapilot-web` | RDS 상태·`db-secret` 값 확인 (랭킹은 영향 없음 — 장애 격리) |
| Pod `OOMKilled` | `kubectl top pods`, `free -m` | 스캐너와 웹 동시 실행 시 피크. Alloy 제거 또는 limits 조정 |
| CI `secrets` 잡 실패 | gitleaks 로그의 파일·커밋 | 진짜 키면 **먼저 키를 폐기·재발급**(히스토리에 남은 키는 지워도 이미 유출된 것으로 간주), 오탐이면 `.gitleaksignore`에 fingerprint 추가 |
| CD의 SSM 단계 실패 | Actions 로그의 stdout/stderr | `aws ssm describe-instance-information`이 Online인지, 인스턴스 Name 태그가 `infrapilot`인지 |
