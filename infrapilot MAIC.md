제시해주신 Terraform 프로젝트 구조와 각 컴포넌트의 역할을 바탕으로, 가독성 높게 정리한 인프라 구성 문서(Markdown)입니다.

---

# 🏗️ Terraform 기반 AWS 인프라 구축 가이드

이 문서는 VPC 네트워크 구성부터 NAT 인스턴스를 통한 Private Subnet 관리, RDS 및 Lambda 배포까지의 전체적인 IaC(Infrastructure as Code) 구조를 설명합니다.

---

## 📁 프로젝트 파일 구조 (File Structure)

Plaintext

```
iac/
├── main.tf                          # 루트 모듈: 모든 모듈의 조립 및 의존성 관리
├── variables.tf                     # 루트 변수: CIDR, AZ, DB 패스워드 등 공통 설정
├── output.tf                        # 최종 출력값: 퍼블릭 IP, 접속 명령어 등
├── provider.tf                      # AWS 프로바이더 및 환경 설정
└── modules/
    ├── vpc/                         # 네트워크 인프라 (VPC, Subnet, IGW, RT)
    ├── compute/                     # 컴퓨팅 리소스 (K3s EC2, NAT 인스턴스)
    ├── rds/                         # Database (PostgreSQL RDS)
    ├── lambda/                      # Serverless (Lambda 함수 + EventBridge)
    ├── ecr/                         # Container Registry (Docker 이미지 저장소)
    ├── security/                    # K3s EC2용 보안그룹
    ├── nat_security/                # NAT 인스턴스 전용 보안그룹
    ├── lambda_security/             # Lambda 함수용 보안그룹
    └── rds_security/                # RDS 데이터베이스용 보안그룹
```

---

## 🏗️ 핵심 아키텍처 및 의존성 흐름

`iac/main.tf`는 오케스트레이터로서 각 모듈을 연결하며, 특히 **순환 의존성(Circular Dependency) 방지**를 위해 다음과 같은 흐름으로 설계되었습니다.

1. **네트워크**: `vpc` 생성
    
2. **보안**: `nat_security` 생성 (VPC ID 참조)
    
3. **컴퓨팅**: `compute` 생성 (NAT 보안그룹 참조)
    
4. **라우팅**: `aws_route` (루트에서 직접 생성)
    
    - **이유**: Private RT(VPC 모듈)와 NAT ENI(Compute 모듈)가 서로를 참조해야 하므로, 순환 의존성을 피하기 위해 루트 모듈에서 경로를 주입합니다.
        

---

## 🛠️ 모듈별 상세 역할

### 1. [Network] `modules/vpc/`

인프라의 논리적 뼈대를 구성합니다.

|**리소스**|**역할**|
|---|---|
|**aws_vpc**|기본 네트워크망 생성 (10.0.0.0/16)|
|**aws_subnet (x4)**|Public 2개(EC2/NAT용), Private 2개(RDS/Lambda용) 분할|
|**aws_internet_gateway**|Public Subnet의 외부 인터넷 연결 허용|
|**aws_route_table (Public)**|IGW를 향한 라우팅 경로 설정|
|**aws_route_table (Private)**|초기 생성 시 빈 상태 유지, 이후 루트에서 NAT 경로 주입|

### 2. [Security] 보안그룹 (Security Groups)

용도별로 보안그룹을 분리하여 **최소 권한 원칙**을 준수합니다.

- **nat_security**:
    
    - **Inbound**: VPC 내부(10.0.0.0/16) 전체 트래픽 허용 (Private Subnet의 외부 통신 대행)
        
    - **Outbound**: 0.0.0.0/0 (인터넷 통신)
        
- **security (K3s EC2)**:
    
    - SSH(22), K3s API(6443), HTTP/HTTPS를 사용자 IP(`my_ip`)에서만 허용
        
- **rds_security**:
    
    - PostgreSQL(5432) 포트를 EC2 SG 및 Lambda SG에서만 접속 가능하도록 제한
        
- **lambda_security**:
    
    - VPC 내부의 RDS 접속을 위해 아웃바운드 위주 설정
        

### 3. [Compute] `modules/compute/`

실제 연산을 담당하는 인스턴스 레이어입니다.

- **K3s 서버 (`pilot_ec2`)**: Ubuntu arm64 (t4g.micro) 기반 경량 쿠버네티스 노드.
    
- **NAT 인스턴스 (`nat`)**: Ubuntu x86_64 (t3.micro) 기반.
    
    - `source_dest_check = false`: 자신에게 오지 않은 패킷도 포워딩하도록 설정.
        
    - `user_data`: 실행 시 `iptables MASQUERADE` 규칙을 자동 적용하여 NAT 기능 활성화.
        

### 4. [Database & Serverless] `rds`, `lambda`, `ecr`

- **rds**: 비용 절감을 위해 단일 인스턴스로 구성된 PostgreSQL. Private Subnet에 배치되어 외부 접근이 차단됩니다.
    
- **ecr**: Lambda에서 사용할 컨테이너 이미지를 저장합니다. 이미지 30개 초과 시 자동으로 삭제하는 Lifecycle Policy가 포함되어 있습니다.
    
- **lambda**: 컨테이너 기반 데이터 수집기입니다. EventBridge를 통해 5분마다 트리거되며, RDS 연동을 위해 VPC 내부에 배치됩니다.
    

---

## 💡 주요 설계 특징 요약

1. **비용 최적화**: AWS NAT Gateway 대신 **NAT Instance**를 사용하고, RDS Multi-AZ를 비활성화하여 초기 비용을 최소화했습니다.
    
2. **보안 강화**: 모든 주요 리소스(RDS, Lambda)를 **Private Subnet**에 배치하고, 보안그룹 간 참조 방식을 통해 불필요한 포트 노출을 방지했습니다.
    
3. **관리 효율성**: 인프라의 확장성을 위해 보안그룹과 네트워크 구조를 각 모듈로 엄격히 분리하여 운영 환경의 독립성을 확보했습니다.3