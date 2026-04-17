
  [IaC] Ansible Role로 구축하는 고성능·보안 서버 인프라 (TCP 튜닝부터 K3s까지)

  안녕하세요! 이번 포스팅에서는 InfraPilot 프로젝트의 핵심인 인프라 자동화
  과정을 다뤄보려 합니다. 단순히 서버를 띄우는 것에 그치지 않고, 확장성과 보안,
  그리고 성능 최적화를 위해 Ansible Role을 어떻게 구성했는지 공유합니다.

  🏗️ 1. 구조 설계: "단단한 바닥부터 서비스까지"

  저는 역할(Role)을 세 단계로 분리하여 인프라의 생명주기를 관리했습니다.

   1. base-hardening: 운영 필수 패키지 설치 및 환경 표준화
   2. network-config: 커널 최적화 및 다중 방어 보안 설정 (핵심!)
   3. k3s-install: 경량 쿠버네티스 엔진 배포

  ---

  🛡️ 2. Role별 구현 디테일

  4) base-hardening: 일관성 있는 환경 구축
  모든 서버에서 동일한 도구를 사용할 수 있도록 apt 모듈을 사용해 필수 패키지를
  설치합니다.

    1 # iac/ansible/roles/base-hardening/tasks/main.yaml
    2 - name: install essential packages
    3   ansible.builtin.apt:
    4     name: [curl, git, htop, net-tools, jq, python3-pip]
    5     state: present
    6     update_cache: true
    7     cache_valid_time: 3600
    8   register: apt_status
    9   retries: 5
   10   until: apt_status is success
   * Point: 네트워크 불안정으로 인한 설치 실패를 방지하기 위해 retries와 until을
     사용하여 자동 복구(Self-healing)가 가능한 태스크를 작성했습니다.

  2) network-config: 고성능과 보안의 조화
  이 Role에서는 서버의 네트워크 성능을 극대화하고 외부 공격을 차단하는 설정을
  담았습니다.

   * TCP 커널 튜닝: 트레이딩 데이터 처리 등 빠른 네트워크 응답이 필요한 환경을
     고려해 sysctl 설정을 적용했습니다.
       * tcp_fastopen: 연결 설정 단계의 오버헤드 감소
       * tcp_tw_reuse: TIME_WAIT 소켓 재사용으로 자원 고갈 방지
       * Keepalive 최적화: 죽은 연결을 빠르게 감지하여 리소스 반환

   * NTP (Chrony) 정밀 동기화:

   1     server 169.254.169.123 prefer iburst  # AWS 내부 NTP 우선 사용
   2     makestep 1.0 3                         # 오차가 클 경우 즉시 보정
       * Insight: 로그 분석과 타임스탬프가 중요한 시스템에서 시간 동기화는
         필수입니다. 특히 클라우드 환경(AWS)의 내부 NTP 서버를 활용해 정밀도를
         높였습니다.

   * 보안 계층화:
       * UFW: 기본 Deny 정책을 바탕으로 22, 80, 443, 6443(K3s API) 포트만 최소
         허용
       * SSH Hardening: Password 인증 및 Root 로그인 차단으로 공격 표면 제거
       * Fail2ban: 무차별 대입 공격 발생 시 IP를 자동으로 차단하는 방어막 구축

  3) k3s-install: 멱등성을 고려한 플랫폼 배포
  마지막으로 실제 워크로드가 올라갈 K3s를 설치합니다.

   1 - name: install k3s
   2   ansible.builtin.shell: |
   3     curl -sfL https://get.k3s.io | sh -
   4   args:
   5     creates: /usr/local/bin/k3s
   * Point: creates 옵션을 사용하여 K3s가 이미 설치된 경우 스크립트 실행을
     건너뛰록 설계했습니다. 이는 멱등성(Idempotency)을 보장하는 중요한
     습관입니다.

  ---

  💡 3. 이번 작업을 통해 배운 점

   1. 운영 안정성: fail2ban과 SSH 설정을 자동화함으로써 인적 실수로 인한 보안
      사고를 원천 차단할 수 있었습니다.
   2. 성능 최적화: 커널 파라미터 튜닝이 단순 설정값을 넘어 시스템 리소스 관리에
      어떤 영향을 주는지 깊이 있게 이해하게 되었습니다.
   3. 가시성: ansible.builtin.debug를 통해 설치된 노드의 상태를 즉시 확인하며
      자동화의 신뢰도를 높였습니다.

  🏁 마치며

  인프라를 코드로 관리한다는 것은 단순히 편리함을 넘어, "인프라의 품질을 코드로
  증명하는 과정"이라는 것을 느꼈습니다. 이번에 구축한 표준 Role들은 앞으로 어떤
  프로젝트에서도 재사용할 수 있는 저만의 강력한 자산이 될 것입니다!

  ---
  Tags: #Ansible #IaC #DevSecOps #TCPTuning #K3s #Sysctl #LinuxSecurity
  #취준생기록
