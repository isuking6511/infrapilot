"""analysis_core 공용 데이터 계약 (dataclass only).

이 파일은 순수 '자료형'만 정의한다. 계산 로직 없음, 외부 import 없음(numpy/pandas도 X).
- 왜 분리: pivots/neely/ict/confluence/setup/scanner가 모두 이 타입들을 주고받는다.
  타입을 한 곳에 고정해야 모듈 간 결합이 시그니처로만 이뤄지고(단방향), 골든
  테스트 스냅샷도 안정된다.
- 왜 dataclass: 결과를 '데이터'로만 반환한다는 원칙(그리기/DB/IO 금지)을 타입으로 강제.
- 왜 ts를 int(epoch ms)로: datetime/Timestamp는 타임존·로캘에 따라 표현이 흔들려
  결정성을 깬다. 정수 epoch ms는 같은 입력→같은 표현을 보장하고 직렬화도 단순.
  (research/backtest 의 Pivot.ts(pd.Timestamp)를 운영용으로 정정한 부분)

원본 매핑(research/backtest/wcse_backtest/ → 여기):
  structure.Pivot           → Pivot
  confluence.SoftLevel + (cand_prices/cand_w/cand_src 병렬리스트) → LevelCandidate
  confluence.Cluster        → LevelCluster
  confluence.SRC_* / SRC_NAMES → LevelSource (IntEnum)
  (신규) 모듈 최종 산출물    → AnalysisResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# 피벗 종류 — research가 1/-1 정수를 쓰므로 그 관례를 그대로 보존(포팅 충실성).
PT_HIGH = 1
PT_LOW = -1


class LevelSource(IntEnum):
    """레벨 후보의 출처. 정본 wcse_v8 engine.py 의 src 코드값을 그대로 보존.

    v8 매핑(Level.src 주석 + DynLine.src + 코드):
      1꼬리 2피보 3넥라인 4돌파선 5빗각/부채살 6FVG 7포크 8가격대 9OB 10LT/리밸런스 11고거래량
    ※ v3_19와 다름: v3_19은 9=유동성(LIQ)/10=OB 였으나, v8엔 유동성 합류레벨이 없고
      (스윕은 setup 트리거로 이동) 9=OB, 10=LT/리밸런스, 11=고거래량으로 재배치됨.

    왜 IntEnum: v8이 정수 코드라 값 호환(골든 스냅샷)이 필요하고 동시에 이름으로 읽혀야 함.
    ict(생성: OB/FVG/넥라인/리밸런스/고거래량)·neely(FIB)·confluence(클러스터)·setup이
    공유하는 어휘라 계약(models)에 둔다.
    """
    WICK = 1     # 꼬리
    FIB = 2      # 피보(되돌림/확장 사다리)
    NECK = 3     # 넥라인(돌파된 구조 레벨 역할전환)
    DON = 4      # 돌파선(Donchian, 동적·비소멸)
    DIAG = 5     # 빗각/부채살(diagonal·fan, 동적·비소멸)
    FVG = 6      # Fair Value Gap
    FORK = 7     # 피치포크(동적·비소멸)
    ZONE = 8     # 가격대(분류된 피벗 수평선)
    OB = 9       # 오더블록(BOS 직전 반대색 캔들)
    LT_REB = 10  # LT 레벨 / 리밸런스 극점(FVG 충전)
    VOL = 11     # 고거래량 캔들 몸통 경계

    @property
    def label(self) -> str:
        """합류 라벨 표기. v8 패널 표기와 동일."""
        return _SRC_LABELS[self]


_SRC_LABELS: dict[LevelSource, str] = {
    LevelSource.WICK: "꼬리", LevelSource.FIB: "피보", LevelSource.NECK: "넥라인",
    LevelSource.DON: "돌파선", LevelSource.DIAG: "빗각", LevelSource.FVG: "FVG",
    LevelSource.FORK: "포크", LevelSource.ZONE: "가격대", LevelSource.OB: "OB",
    LevelSource.LT_REB: "LT", LevelSource.VOL: "고거래량",
}

# src_text 출력 순서 — 강한 근거→약한 근거. 우리 표시 규약(v8 Pine 라벨은 단일 고정
# 리스트가 아니므로 here에서 결정성 위해 고정). 스냅샷 안정 목적.
_SRC_ORDER: tuple[LevelSource, ...] = (
    LevelSource.FIB, LevelSource.DIAG, LevelSource.FORK, LevelSource.ZONE,
    LevelSource.OB, LevelSource.LT_REB, LevelSource.NECK, LevelSource.DON,
    LevelSource.WICK, LevelSource.FVG, LevelSource.VOL,
)


@dataclass
class Candle:
    """단일 OHLCV 봉. analysis_core의 유일한 입력 단위.

    데이터소스(Bybit/Upbit/코스피/나스닥) 무관하게 collectors가 Candle 리스트로
    변환해 넘긴다(CLAUDE.md §6: 입력이 Candle이면 소스 무관). 시간순 정렬 가정.
    """
    ts: int          # epoch milliseconds (봉 시작 시각)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Pivot:
    """확정된 스윙 고/저점. structure.Pivot 포팅.

    bar: 이 피벗이 '사는' 봉 인덱스(좌우 N봉 확정지연을 반영한 시점, lookahead 없음).
    classified: 0=일반 스윙, 1=ITH/ITL(3피벗 규칙 통과). research 관례 보존.
    """
    bar: int
    ts: int               # 해당 봉 epoch ms
    price: float
    ptype: int            # PT_HIGH(1) | PT_LOW(-1)
    classified: int = 0


@dataclass
class LevelCandidate:
    """클러스터링 이전의 개별 레벨 후보(가격+가중치+출처).

    research에서는 SoftLevel + (cand_prices/cand_w/cand_src) 병렬 리스트로 흩어져
    있던 것을 하나의 타입으로 통합. active는 SoftLevel의 consume-on-touch(닿으면
    소멸) 개념을 보존 — 비활성 후보는 클러스터링에서 제외.
    """
    price: float
    weight: float
    source: LevelSource
    active: bool = True


@dataclass
class LevelCluster:
    """tol 내 후보들을 묶은 합류 클러스터. confluence.Cluster 포팅.

    mid: 가중평균 가격, score: 가중치 합(=핵심 랭킹 신호), cnt: 구성원 수,
    sources: 기여한 출처 집합. src_text는 고정 순서로 파생(스냅샷 안정).
    """
    mid: float
    score: float
    cnt: int
    sources: frozenset[LevelSource] = field(default_factory=frozenset)

    @property
    def src_text(self) -> str:
        return " ".join(s.label for s in _SRC_ORDER if s in self.sources)


@dataclass
class Setup:
    """진입 계획 — '데이터만' 담는 타입. 판정 로직(임계 비교·게이트)은 setup.py 모듈 함수가
    하고, 이 dataclass는 결과만 보관(타입 vs 모듈 분리).

    채택 v4 reversion 산출물(단일 TP):
      - 진입 = 합류 클러스터 중심(지정가) / 손절 = 보호 스윙±버퍼(or stopN·ATR)
      - target = 진입+리스크 너머 최근접 합류(없으면 rrMin 배). rr은 target 기준.
      - mode = 엔진 식별 문자열(예: 'v4_reversion').
    ※ 사다리(tp2/tp3)는 v8 같은 사다리형 엔진을 실제 추가할 때 필드를 더한다(YAGNI —
      지금 안 쓰는 필드 미리 두지 않음).
    """
    direction: int       # +1 long, -1 short
    entry: float         # 진입가 (= 합류 클러스터 mid, 지정가)
    stop: float          # 손절가 (파동 이탈점)
    target: float        # 목표가
    rr: float            # R = |target-entry| / |entry-stop|
    score: float         # 합류 점수 (선택된 클러스터 score)
    src_text: str = ""   # 합류 근거 (클러스터 구성 소스, 예: "피보 OB LT")
    mode: str = "v4_reversion"


@dataclass
class AnalysisResult:
    """한 (심볼 × 타임프레임)에 대한 엔진 최종 산출물. analysis_core의 반환 계약.

    lambda_/scanner/dashboard가 소비하는 객체. 결정성 보장: 같은 Candle 리스트 →
    같은 AnalysisResult. 외부에 데이터로만 노출(그리기/DB/IO는 소비자 책임).

    as_of_ts: 마지막 봉의 epoch ms. 결정성 기준점이자 Redis 캐시 키 구성요소.
    direction: 현재 바이어스(+1/-1/0). score: 스캐너 랭킹 점수.
    setup: setup.py가 채우는 진입 계획. 셋업 없으면 None(매끄러운 데이터에선 정상).
    """
    symbol: str
    timeframe: str
    as_of_ts: int
    close: float
    atr: float

    direction: int = 0                # +1 long bias / -1 short bias / 0 중립
    score: float = 0.0                # 랭킹 점수 (scanner가 이 값으로 내림차순 정렬)

    pivots: list[Pivot] = field(default_factory=list)
    clusters: list[LevelCluster] = field(default_factory=list)

    setup: Optional[Setup] = None     # 진입 계획 (없으면 None)
    notes: str = ""
