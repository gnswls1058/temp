# 검색 평가용 테스트 코퍼스 (NexBridge Corporation)

가상 IT 기업 **NexBridge Corporation** 의 개발 조직이 2024~2026년에 걸쳐 작성한 것처럼 구성한
Confluence 문서 300건입니다. 사내 검색, Entity 검색, 유사 문서 탐색, 최신 문서 탐색,
Reranking, 문서 요약 평가에 사용합니다.

모든 인물, 회사, 시스템, 데이터는 가상입니다. 실제 개인정보나 인증 정보는 포함되어 있지 않습니다.

## 게시 위치

| 항목 | 값 |
| --- | --- |
| 스페이스 | `NEXBRIDGE` (NexBridge Corporation) |
| 문서 수 | 300 |
| 부모 페이지 | 6 (프로젝트 5 + 공통) |

문서는 프로젝트별 부모 페이지 아래에 배치됩니다.

## 구성

### 문서 유형 (§8 배분 준수)

| 유형 | 문서 수 |
| --- | ---: |
| 개발자 회의록 | 45 |
| API 명세서 | 40 |
| 기획자와의 회의록 | 35 |
| 이슈 정리 | 35 |
| 프로젝트 진행상황 | 30 |
| 코드리뷰 | 30 |
| 매뉴얼 / 정의서 | 25 |
| DB 테이블 설계안 | 25 |
| 비즈니스 기능 설계 | 25 |
| 팀 Ground Rule | 10 |
| **합계** | **300** |

### 시스템

| 시스템 | 담당 |
| --- | --- |
| NEXUS | 회원, 로그인, 프로필, 계정 |
| ORBIT | 상품, 주문, 결제, 취소, 환불, 배송 |
| MARS | 포인트, 쿠폰, 리워드, 이벤트 보상 |
| AIMS | 운영자 Admin |
| BlueGate | 인증, JWT, OAuth, 접근 제어 |

### 프로젝트와 시기

| 프로젝트 | 주제 | 시기 | 문서 수 |
| --- | --- | --- | ---: |
| Project Echo | 인증 시스템 개선 (BlueGate) | 2024 H2, 2026 재개 | 19 |
| Project Aurora | 회원가입 개편 (NEXUS) | 2025 H1 | 28 |
| Project Nova | 리워드 플랫폼 개선 (MARS) | 2025 H2, 2026 재개 | 39 |
| Project Falcon | 결제 시스템 고도화 (ORBIT) | 2025 Q4 ~ 2026 Q1 | 31 |
| Project Atlas | Admin 개편 (AIMS) | 2026 Q2~Q3 | 31 |
| (공통) | 인프라, 규칙, 도메인 전반 | 전 기간 | 152 |

## 스토리 연결

문서는 독립적이지 않습니다. 대표적인 연결 흐름입니다.

**설계 변경 추적 (Nova)**

```
DOC-048 reward 테이블 설계안 (잔액 컬럼 A안, deprecated)
  → DOC-057 동시 주문 시 잔액 음수 문제
  → DOC-058 잔액 관리 방식 재검토 (ledger 도입 논의)
  → DOC-061 reward_balance / reward_history 분리 설계 (B안 확정)
  → DOC-075 Nova 배포 후 회고
```

**결제 멱등성 (Falcon)**

```
DOC-077 Falcon 킥오프
  → DOC-080 결제 승인 API v2 초안
  → DOC-084 [리뷰] PaymentService transaction 범위
  → DOC-091 QA 중 동일 주문 2회 승인 확인
  → DOC-092 멱등키 생성 주체와 범위 정리
  → DOC-093 결제 멱등성 처리 설계
  → DOC-095 결제 승인 API v2.1 변경 내역
  → DOC-103 PG 지연 후 동일 주문 이중 승인
  → DOC-107 Falcon 배포 후 회고
```

**미확정 항목이 프로젝트를 넘어감**

```
DOC-066 부분 취소 시 리워드 회수 (미확정, Nova)
  → DOC-072 부분 환불 시 회수 기준 논의 (여전히 미확정)
  → DOC-098 부분 취소 시 적립 회수 기준 확정 (Falcon 에서 종결, 4개월 소요)
```

**임시 조치가 장기화**

```
DOC-126 온콜 전용 공용 계정 (임시 조치)
  → DOC-135 Atlas 회고에서 과제로 지적
  → DOC-287 임시 권한 위임 설계 검토 (4개월 뒤)
  → DOC-293 공용 계정 폐기 예정
```

## 검색 평가용 특성

- **버전 관계**: 로그인 API v1(DOC-004) / 토큰 재발급 v1(DOC-006) 은 `deprecated`,
  v2(DOC-257)가 최신. 2024 코드리뷰 규칙(DOC-012) → 2026 개정판(DOC-109).
- **유사하지만 다른 문서**: 결제 실패 처리 정책(DOC-226) / PG 장애 대응 가이드(DOC-228) /
  결제 재시도 정책 설계(DOC-229) — 주제가 겹치되 관점과 독자가 다름.
- **제목과 본문 표현 차이**: "캐시 서버 연결 수가 계속 늘어나는 현상"(DOC-136)의 본문은 Redis,
  "주문 저장 중 교착 상태"(DOC-137)의 본문은 Deadlock, "카드사 거절 사유가 안내되지 않는
  문제"(DOC-227)의 본문은 매핑 표와 `declineCategory`.
- **미해결 문서**: 일부 이슈는 조사 중이거나 정책 대기 상태로 종료되지 않습니다.
- **시점 분포**: 2024년 15건, 2025년 111건, 2026년 174건.

## Ground Truth

`corpus/ground_truth.json` 에 문서별 정답 메타데이터가 있습니다. **Confluence 본문에는
포함되지 않습니다.**

```json
{
  "documentId": "DOC-091",
  "title": "QA 중 동일 주문 2회 승인 확인",
  "department": "QA팀",
  "documentType": "이슈 정리",
  "project": "Project Falcon",
  "entities": ["ORBIT"],
  "features": ["결제"],
  "participants": ["윤수빈", "김도윤"],
  "concepts": ["중복 요청", "멱등성", "Race Condition"],
  "dates": ["2026-01-09"],
  "isDeprecated": false,
  "pageId": "...",
  "bodyLength": 912
}
```

## 도구

```bash
python tools/validate_corpus.py       # 배분, ID, 인물, 길이 검증
python tools/publish_corpus.py        # Confluence 게시 (변경분만 갱신)
python tools/publish_corpus.py --dry-run
python tools/publish_corpus.py --only DOC-001 DOC-002
python tools/export_ground_truth.py   # Ground Truth 내보내기
```

게시 상태는 `corpus/publish_state.json` 에 기록됩니다 (DOC-ID → pageId 매핑, 본문 해시).
본문이 바뀐 문서만 갱신되므로 반복 실행해도 안전합니다.

## 문서 추가/수정

`corpus/docs/batch_*.py` 의 `DOCUMENTS` 리스트에 레코드를 추가합니다.

```python
{
    "id": "DOC-301",
    "title": "...",
    "department": "백엔드개발팀",
    "type": "개발자 회의록",
    "project": "Project Falcon",   # 또는 None
    "date": "2026-09-01",
    "systems": ["ORBIT"],
    "features": ["결제"],
    "participants": ["김도윤"],     # 본문에 등장하는 인물은 반드시 여기 포함
    "concepts": ["멱등성"],
    "deprecated": False,
    "body": """## 논의 배경\n...""",   # 경량 Markdown
}
```

본문은 경량 Markdown 으로 쓰고 `tools/markdown_to_storage.py` 가 Confluence storage format
으로 변환합니다. 제목(`##`), 목록, 표, 코드 블록, 인용, 인라인 코드/굵게를 지원합니다.

검증기가 다음을 확인합니다.

- ID 중복 및 형식
- 제목 중복 (Confluence 는 스페이스 내 제목이 유일해야 함)
- 카테고리 배분
- 본문에 등장하는 인물이 `participants` 에 있는지
- 인물 등장 비율 40~60%
- 본문 길이 분포
