---
title: "Terraform으로 AWS VPC + EC2 한 번에 프로비저닝하기 — 모듈 구조까지"
date: 2026-04-14
tags: [Terraform, AWS, VPC, EC2, IaC, DevOps]
status: draft
---

# Terraform으로 AWS VPC + EC2 한 번에 프로비저닝하기

## 왜 Terraform인가?

AWS 콘솔에서 클릭클릭으로 VPC 만들고 EC2 올리는 건 한 번이면 충분하다.  
두 번째 환경(dev, staging, prod)이 필요한 순간부터 **IaC(Infrastructure as Code)** 가 필요하다.

Terraform은 HCL(HashiCorp Configuration Language)로 인프라를 코드로 선언한다.  
`terraform apply` 한 번으로 VPC → 서브넷 → 라우팅 → EC2까지 순서대로 만들어준다.

---

## 전체 구조

```
iac/
├── main.tf          # 모듈 조합
├── variables.tf     # 변수 정의
├── output.tf        # 출력값
└── modules/
    ├── vpc/         # 네트워크 레이어
    ├── security/    # 보안 그룹
    └── compute/     # EC2
```

모듈로 분리한 이유 — 역할별로 나누면 재사용이 쉽고, 어디서 에러가 났는지 바로 보인다.

---

## 1단계: VPC + 네트워크 구성

```hcl
resource "aws_vpc" "pilot_vpc" {
  cidr_block         = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
}
```

`enable_dns_hostnames = true` — EC2에 퍼블릭 DNS 이름이 붙는다.  
없으면 IP로만 접근해야 하니 켜두는 게 기본.

### 서브넷 2개 (멀티 AZ)

```hcl
resource "aws_subnet" "public_subnet1" {
  vpc_id            = aws_vpc.pilot_vpc.id
  cidr_block        = var.subnet1_cidr
  availability_zone = var.az_a
}

resource "aws_subnet" "public_subnet2" {
  vpc_id            = aws_vpc.pilot_vpc.id
  cidr_block        = var.subnet2_cidr
  availability_zone = var.az_b
}
```

서브넷을 2개, 다른 AZ에 두는 이유 — 나중에 ALB나 RDS를 붙일 때 멀티 AZ가 필수 조건이다.  
지금은 EC2 하나지만, 미래를 위해 미리 준비.

### 인터넷 게이트웨이 + 라우팅

```hcl
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.pilot_vpc.id
}

resource "aws_route_table" "pilot_public_rt" {
  vpc_id = aws_vpc.pilot_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
}

resource "aws_route_table_association" "public_1" {
  subnet_id      = aws_subnet.public_subnet1.id
  route_table_id = aws_route_table.pilot_public_rt.id
}
```

VPC를 만들어도 IGW + Route Table이 없으면 인터넷이 안 된다.  
`0.0.0.0/0 → IGW` 라우팅이 "외부로 나가는 길"이다.

---

## 2단계: Security Group — 최소 권한 원칙

```hcl
# SSH — 내 IP만
resource "aws_vpc_security_group_ingress_rule" "allow_ssh" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = var.my_ip   # 내 IP/32
  from_port         = 22
  ip_protocol       = "tcp"
  to_port           = 22
}

# K3s API — 내 IP만
resource "aws_vpc_security_group_ingress_rule" "allow_k8s" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = var.my_ip
  from_port         = 6443
  ip_protocol       = "tcp"
  to_port           = 6443
}

# HTTP/HTTPS — 전체 오픈
resource "aws_vpc_security_group_ingress_rule" "allow_http" {
  security_group_id = aws_security_group.pilot_sg.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  ip_protocol       = "tcp"
  to_port           = 80
}
```

**보안 원칙:** SSH와 K3s API(6443)는 내 IP만, 웹(80/443)만 전체 오픈.  
`from_port == to_port` 는 포트 하나만 여는 것. `from_port=0, to_port=65535`면 전체 오픈이 된다.

---

## 3단계: EC2 배포

```hcl
resource "aws_instance" "pilot_ec2" {
  ami                         = "ami-084a56dceed3eb9bb"  # Ubuntu 24.04
  instance_type               = "t3.micro"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.sg_id]
  key_name                    = var.key_name
  associate_public_ip_address = true   # 필수!

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }
}
```

**`associate_public_ip_address = true`** 를 빠뜨리면 SSH 접속 자체가 불가능하다.  
퍼블릭 IP 없이 퍼블릭 서브넷에 올려도 인터넷에서 도달할 수 없다.

`gp3` — gp2보다 빠르고 저렴한 최신 범용 SSD. 특별한 이유 없으면 gp3.

---

## 4단계: 모듈 조합 (main.tf)

```hcl
module "vpc" {
  source       = "./modules/vpc"
  vpc_cidr     = var.vpc_cidr
  subnet1_cidr = var.subnet1_cidr
  subnet2_cidr = var.subnet2_cidr
  az_a         = var.az_a
  az_b         = var.az_b
}

module "security" {
  source = "./modules/security"
  my_ip  = var.my_ip
  vpc_id = module.vpc.vpc_id   # vpc 모듈의 output 참조
}

module "compute" {
  source    = "./modules/compute"
  subnet_id = module.vpc.public_subnet1_id
  sg_id     = module.security.sg_id
  key_name  = var.key_name
}
```

모듈 간 의존성은 output → input으로 연결한다.  
`module.vpc.vpc_id` — vpc 모듈이 먼저 만들어져야 security가 실행된다.  
Terraform이 이 순서를 자동으로 파악하고 실행한다.

---

## 실행 흐름

```bash
terraform init    # 프로바이더 다운로드
terraform plan    # 변경사항 미리보기 (실제 적용 X)
terraform apply   # 실제 인프라 생성
```

`plan`과 `apply`를 분리한 이유 — plan으로 "무엇이 만들어지는지" 먼저 확인하고,  
확인 후 apply로 실제 적용. 콘솔 클릭과 달리 실수를 사전에 잡을 수 있다.

---

## 트러블슈팅 기록

| 에러 | 원인 | 해결 |
|------|------|------|
| `cidr_ipv4` 에러 | VPC ID를 넣음 | IP 주소/CIDR만 입력 |
| SSH 접속 불가 | `associate_public_ip_address` 누락 | `true`로 설정 |
| SG rule 충돌 | 이름 중복 | 각 rule에 고유한 resource 이름 부여 |

---

## 배운 것 정리

| 개념 | 의미 |
|------|------|
| `terraform plan` | 미리보기 (apply 아님) |
| `terraform apply` | 실제 인프라 생성/변경 |
| IGW + Route Table | 인터넷 연결의 필수 조건 |
| 모듈 output → input | 모듈 간 의존성 연결 방법 |
| `associate_public_ip_address` | 퍼블릭 IP 부여 여부 |

---

## 다음 단계

- Ansible로 K3s 자동 설치
- kubeconfig 설정
- 첫 번째 Pod 배포

---

*InfraPilot 시리즈 — AI 멀티에이전트 자동매매 인프라 구축기*
