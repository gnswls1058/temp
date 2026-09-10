"""DOC-076 ~ DOC-090 : Project Falcon 시작 - 결제 시스템 고도화 (2025 Q4)."""

DOCUMENTS = [
    {
        "id": "DOC-076",
        "title": "결제 실패 화면 처리 방향 논의",
        "department": "서비스기획팀",
        "type": "기획자와의 회의록",
        "project": "Project Falcon",
        "date": "2025-11-18",
        "systems": ["ORBIT"],
        "features": ["결제"],
        "participants": ["강민재", "정하늘"],
        "concepts": ["재시도", "UX", "에러 처리"],
        "body": """## 논의 배경

결제 실패 시 화면이 "결제에 실패했습니다"만 보여줍니다. 사용자가 왜 실패했는지 몰라 같은 카드로 계속 시도하고, 결국 이탈합니다. 고객지원 문의 중 결제 관련이 21% 입니다.

## 실패 유형

정하늘님이 최근 한 달 실패 응답을 분류했습니다.

| 유형 | 비중 | 사용자 조치 |
| --- | --- | --- |
| 한도 초과 | 34% | 다른 카드 |
| 잔액 부족 | 22% | 다른 수단 |
| 카드사 거절 | 18% | 카드사 문의 |
| 유효기간 만료 | 9% | 카드 정보 수정 |
| 시스템 오류 / timeout | 12% | 재시도 |
| 기타 | 5% | |

## 쟁점

강민재님은 유형별로 다른 문구를 보여주자고 했습니다. 정하늘님은 PG 가 내려주는 코드가 카드사마다 달라서 매핑이 완전하지 않다고 지적했습니다. 매핑에 없는 코드가 오면 결국 공통 문구가 나갑니다.

두 번째 쟁점은 재시도입니다. timeout 은 재시도가 유효하지만 한도 초과는 같은 카드로 재시도하면 또 실패합니다. 재시도 버튼을 유형별로 다르게 노출해야 합니다.

## 결정 사항

- 실패 유형을 5개 그룹으로 묶고 그룹별 문구와 액션을 정의한다
- 매핑되지 않은 코드는 "잠시 후 다시 시도" 그룹으로 보낸다
- 한도/잔액 부족은 재시도 버튼 대신 "다른 결제수단" 버튼을 노출한다

## 미확정

timeout 시 자동 재시도를 서버에서 할지 사용자에게 맡길지 정하지 못했습니다. 자동 재시도는 중복 결제 위험이 있어 백엔드 검토가 필요합니다. Falcon 킥오프에서 다룹니다.

## Action Item

- 강민재님: 그룹별 문구안 작성
- 정하늘님: PG 응답 코드 매핑표 정리
""",
    },
    {
        "id": "DOC-077",
        "title": "Falcon 킥오프 - 결제 고도화 범위",
        "department": "백엔드개발팀",
        "type": "개발자 회의록",
        "project": "Project Falcon",
        "date": "2025-11-20",
        "systems": ["ORBIT"],
        "features": ["결제", "결제 취소", "환불"],
        "participants": ["김도윤", "임채원", "강민재"],
        "concepts": ["범위 산정", "멱등성", "PG"],
        "body": """## 논의 배경

결제 코드는 3년 전에 만들어진 뒤 기능이 계속 붙었습니다. 부분 취소가 지원되지 않고, PG timeout 처리가 없으며, 상태값이 7개인데 실제로 쓰이는 것은 4개입니다.

## 범위

포함:

- 결제 승인/취소 API 재설계 (v2)
- 부분 취소 지원
- PG timeout 및 중복 요청 처리
- 결제 상태값 정리
- `payment` 테이블 재설계

제외:

- 신규 결제수단 추가 (간편결제 확대는 별도)
- 정산 로직

## 주요 쟁점

**멱등성**: 지금은 같은 주문에 결제 요청이 두 번 들어오면 두 번 승인됩니다. 실제로 지난달 중복 승인이 3건 있었고 수동 취소했습니다. 김도윤님은 이번에 중복 방지 키를 도입해야 한다는 입장입니다.

**timeout 자동 재시도**: 강민재님이 기획 회의에서 나온 질문을 가져왔습니다. 김도윤님 답은 멱등성이 확보되기 전에는 자동 재시도를 넣으면 안 된다는 것이었습니다. PG 는 승인했는데 응답만 유실된 경우 재시도가 이중 결제가 됩니다.

**부분 취소**: PG 가 부분 취소를 지원하지만 우리 쪽 데이터 모델이 이를 표현하지 못합니다. 취소 이력을 별도 테이블로 뺄지 논의가 필요합니다.

## 결정 사항

- 중복 방지 키를 v2 필수 항목으로 넣는다
- timeout 자동 재시도는 멱등성 구현 이후에 검토한다
- 부분 취소는 이번 범위에 포함한다

## 일정

임채원님이 정리한 초안은 4스프린트, 2026년 2월 배포입니다. 부분 취소가 들어가면서 한 스프린트 늘었습니다.

## Action Item

- 김도윤님: 결제 상태 전이 정의
- 강민재님: 부분 취소 정책 정리. Nova 에서 미확정으로 남은 리워드 회수 기준도 함께 필요
""",
    },
    {
        "id": "DOC-078",
        "title": "결제 승인 처리 설계",
        "department": "백엔드개발팀",
        "type": "비즈니스 기능 설계",
        "project": "Project Falcon",
        "date": "2025-11-25",
        "systems": ["ORBIT", "MARS"],
        "features": ["결제", "주문"],
        "participants": [],
        "concepts": ["멱등성", "Transaction", "외부 시스템 장애", "Edge Case"],
        "body": """## 배경

결제 승인은 외부 PG 호출과 내부 DB 저장이 함께 일어납니다. 둘 사이에 장애가 끼면 돈은 빠져나갔는데 주문은 없는 상태가 됩니다. 실제로 월 1~2건 발생하고 있고, 수동으로 찾아 처리하고 있습니다.

## 정상 Flow

1. 클라이언트가 멱등키와 함께 결제 요청
2. 멱등키로 기존 처리 확인
3. `payment` 행을 `PENDING` 으로 먼저 생성 (승인 전 기록)
4. 적립금/쿠폰 사용 요청 (MARS)
5. PG 승인 요청
6. 승인 결과로 `payment` 를 `APPROVED` 또는 `FAILED` 로 갱신
7. 주문 상태 갱신

## 왜 PENDING 을 먼저 쓰는가

PG 호출 전에 기록을 남겨야 응답이 유실됐을 때 추적할 수 있습니다. 3번이 커밋되어야 5번을 호출하므로 트랜잭션이 나뉩니다.

## Sequence

```text
Client -> ORBIT : approve(idempotencyKey, orderId, amount)
ORBIT  -> DB    : payment(PENDING) 저장 + commit
ORBIT  -> MARS  : 포인트 사용
ORBIT  -> PG    : 승인 요청
PG     -> ORBIT : 승인 결과
ORBIT  -> DB    : payment(APPROVED) + 주문 상태 + commit
ORBIT  -> Client: 결과
```

## Edge Case

- **PG 승인 성공, 내부 저장 실패**: `PENDING` 행이 남습니다. 보정 배치가 `PENDING` 상태로 5분 이상 머문 건을 PG 에 조회해 실제 승인 여부를 확인하고 상태를 맞춥니다.
- **PG timeout**: 승인됐는지 알 수 없습니다. `PENDING` 유지하고 사용자에게는 "처리 중" 안내를 합니다. 보정 배치가 결과를 확정합니다. 자동 재시도는 하지 않습니다.
- **중복 요청**: 멱등키가 같으면 기존 결과를 반환합니다. 처리 중이면 409 와 함께 재시도 안내를 줍니다.
- **적립금 사용 성공, PG 실패**: 적립금을 되돌려야 합니다. MARS 사용 취소를 호출하고, 이 호출이 실패하면 재시도 큐에 넣습니다. 적립금이 묶인 채로 남는 것이 가장 나쁜 상황이라 큐 처리 실패는 알림을 보냅니다.
- **승인 직후 주문 취소 요청**: 주문 상태 갱신 전에 취소가 들어오면 취소할 결제를 못 찾습니다. 주문 취소는 `payment` 상태가 확정될 때까지 대기시킵니다.
- **동일 주문에 다른 멱등키**: 사용자가 결제 화면을 새로 열면 멱등키가 새로 생성됩니다. 주문 단위로도 승인 이력을 확인해 이미 승인된 주문이면 거부합니다.

## 고려한 방법

PG 호출과 DB 저장을 하나의 트랜잭션으로 묶는 방안은 불가능합니다. 외부 호출은 롤백할 수 없습니다.

2단계 커밋 대신 상태 기반 보정(`PENDING` + 조회 배치)을 선택했습니다. 완벽하지는 않지만 구현이 단순하고, 보정 배치가 실패해도 데이터가 남아 수동 처리가 가능합니다.
""",
    },
    {
        "id": "DOC-079",
        "title": "payment 테이블 재설계",
        "department": "백엔드개발팀",
        "type": "DB 테이블 설계안",
        "project": "Project Falcon",
        "date": "2025-11-27",
        "systems": ["ORBIT"],
        "features": ["결제", "결제 취소"],
        "participants": [],
        "concepts": ["상태값 관리", "history 테이블", "Unique Constraint", "부분 취소"],
        "body": """## 배경

현재 `payment` 는 결제 한 건에 한 행이고, 취소도 같은 행을 갱신합니다. 부분 취소를 여러 번 하면 표현할 방법이 없습니다.

## 현재 문제

- 취소 금액 컬럼이 하나뿐이라 부분 취소 이력이 덮어써집니다
- 상태값 7개 중 `READY`, `WAITING`, `HOLD` 는 사용되지 않습니다
- 멱등키 컬럼이 없습니다
- PG 응답 원문을 저장하지 않아 장애 분석이 어렵습니다

## payment

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| id | BIGINT | PK | |
| order_id | BIGINT | NOT NULL | |
| idempotency_key | VARCHAR(64) | UNIQUE | 클라이언트 생성 |
| status | VARCHAR(16) | NOT NULL | PENDING / APPROVED / FAILED / CANCELED / PARTIAL_CANCELED |
| method | VARCHAR(16) | NOT NULL | CARD / TRANSFER / EASY_PAY |
| total_amount | INT | NOT NULL | 결제 총액 |
| canceled_amount | INT | NOT NULL DEFAULT 0 | 누적 취소 금액 |
| point_amount | INT | NOT NULL DEFAULT 0 | 적립금 사용액 |
| pg_transaction_id | VARCHAR(64) | NULL | PG 거래번호 |
| approved_at | DATETIME | NULL | |
| created_at | DATETIME | NOT NULL | |

## payment_history

승인, 취소, 실패 등 모든 시도를 기록합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | BIGINT | PK |
| payment_id | BIGINT | FK |
| event_type | VARCHAR(24) | APPROVE_REQUESTED / APPROVED / APPROVE_FAILED / CANCEL_REQUESTED / CANCELED |
| amount | INT | 해당 이벤트 금액 |
| pg_response | TEXT | PG 응답 원문 (카드번호 등 민감정보 마스킹 후) |
| created_at | DATETIME | |

```sql
CREATE UNIQUE INDEX uk_payment_idem ON payment (idempotency_key);
CREATE INDEX idx_payment_order ON payment (order_id);
CREATE INDEX idx_payment_pending ON payment (status, created_at);
CREATE INDEX idx_history_payment ON payment_history (payment_id, created_at);
```

## 왜 history 를 분리하는가

부분 취소가 여러 번 일어나면 이력이 필요합니다. 또 장애 분석에서 "언제 무엇을 요청했고 PG 가 뭐라고 답했는지"가 가장 중요한 정보인데 지금은 남지 않습니다.

`payment` 는 현재 상태만 보여주고, 무슨 일이 있었는지는 `payment_history` 를 봅니다.

## canceled_amount 를 중복으로 두는 이유

이력을 합산하면 구할 수 있지만 주문 목록 화면에서 매번 합산하기 부담스럽습니다. 스냅샷으로 두고 정합성 대조 배치로 검증합니다. 리워드에서 쓴 방식과 같습니다.

## 상태 정리

`READY`, `WAITING`, `HOLD` 는 제거합니다. 운영 데이터에 이 상태인 행이 41건 있는데 모두 3년 전 데이터이고 이후 갱신이 없습니다. 마이그레이션 시 `FAILED` 로 정리합니다.

## pg_response 저장

카드번호, 생년월일 등이 응답에 포함될 수 있습니다. 저장 전 마스킹 처리를 거칩니다. 마스킹 규칙은 정보보안팀 검토가 필요하며 아직 확정 전입니다.
""",
    },
    {
        "id": "DOC-080",
        "title": "결제 승인 API v2 초안",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": "Project Falcon",
        "date": "2025-12-02",
        "systems": ["ORBIT"],
        "features": ["결제"],
        "participants": [],
        "concepts": ["멱등성", "Error Code", "버전 변경"],
        "body": """## 변경 개요

| 항목 | v1 | v2 |
| --- | --- | --- |
| 멱등키 | 없음 | 필수 |
| 처리 중 응답 | 없음 | 202 추가 |
| 부분 취소 | 미지원 | 지원 |
| 적립금 사용 | 별도 호출 | 요청에 포함 |

v1 은 2026년 6월까지 유지합니다.

## Endpoint

```http
POST /payments
Idempotency-Key: {clientGeneratedKey}
Authorization: Bearer {accessToken}
```

멱등키는 헤더로 받습니다. 최대 64자, 클라이언트가 주문당 하나를 생성합니다.

## Request

```json
{
  "orderId": 90112,
  "method": "CARD",
  "totalAmount": 34000,
  "pointAmount": 2500,
  "couponIssueId": 5512099,
  "card": {
    "billingKey": "bk_sample_key"
  }
}
```

`totalAmount` 는 적립금과 쿠폰 할인을 제외한 실제 청구 금액입니다.

## Response

승인 완료 (200):

```json
{
  "paymentId": 771903,
  "status": "APPROVED",
  "approvedAmount": 34000,
  "pgTransactionId": "pg_20251202_0031",
  "approvedAt": "2025-12-02T14:22:31+09:00"
}
```

처리 중 (202):

```json
{
  "paymentId": 771904,
  "status": "PENDING",
  "retryAfterSeconds": 3
}
```

PG timeout 등으로 결과가 확정되지 않은 경우입니다. 클라이언트는 `GET /payments/{paymentId}` 로 확인합니다. **같은 멱등키로 다시 POST 하지 마세요.**

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| PAY_1001 | 400 | 멱등키 누락 또는 형식 오류 |
| PAY_1002 | 409 | 이미 결제된 주문 |
| PAY_1003 | 409 | 동일 멱등키로 처리 중 |
| PAY_1010 | 402 | PG 승인 거절 (상세는 `declineCode`) |
| PAY_1011 | 409 | 적립금 사용 실패 |
| PAY_1012 | 400 | 금액 불일치 (주문 금액과 다름) |

`PAY_1010` 응답에는 `declineCode` 와 `declineGroup` 이 포함됩니다. 화면 문구는 `declineGroup` 기준으로 매핑하세요.

## 미확정

적립금 사용을 요청에 포함시키면 결제 실패 시 되돌리는 책임이 ORBIT 에 생깁니다. MARS 팀과 인터페이스 협의가 끝나지 않아 이 부분은 초안 상태입니다.
""",
    },
    {
        "id": "DOC-081",
        "title": "PG 연동 방식 및 타임아웃 기준 검토",
        "department": "백엔드개발팀",
        "type": "개발자 회의록",
        "project": "Project Falcon",
        "date": "2025-12-04",
        "systems": ["ORBIT"],
        "features": ["결제"],
        "participants": ["김도윤", "오세훈"],
        "concepts": ["Timeout", "외부 시스템 장애", "Circuit Breaker"],
        "body": """## 논의 배경

현재 PG 호출 타임아웃이 60초입니다. 이 시간 동안 스레드가 잡혀 있어서 PG 가 느려지면 서비스 전체가 같이 느려집니다. 지난 10월 PG 지연 때 실제로 그랬습니다.

## 현황

| 항목 | 현재 값 |
| --- | --- |
| connect timeout | 5초 |
| read timeout | 60초 |
| 재시도 | 없음 |
| 커넥션 풀 | 50 |

PG 평균 응답은 1.2초, p99 는 4.8초입니다. 60초는 근거 없이 크게 잡힌 값입니다.

## 논의 내용

오세훈님은 read timeout 을 10초로 줄이자고 제안했습니다. p99 의 두 배 정도면 정상 요청은 대부분 통과합니다.

김도윤님은 타임아웃을 줄이면 `PENDING` 상태가 늘어난다고 지적했습니다. 실제로는 승인됐는데 우리가 못 기다린 경우가 생깁니다. 보정 배치가 이를 처리하지만 사용자는 "처리 중" 화면을 보게 됩니다.

절충안으로 승인은 15초, 조회는 5초로 나누기로 했습니다. 승인은 놓치면 비용이 크고, 조회는 실패해도 재시도가 안전합니다.

## Circuit Breaker

PG 오류율이 임계값을 넘으면 차단하고 즉시 실패시키는 방안을 검토했습니다. 다만 결제는 차단해도 사용자가 할 수 있는 게 없어서 실익이 적습니다. 대신 커넥션 풀을 분리해 PG 지연이 다른 기능에 번지지 않게 하는 쪽으로 정리했습니다.

## 결정 사항

- 승인 read timeout 15초, 조회 5초
- PG 전용 커넥션 풀 분리
- Circuit Breaker 는 도입하지 않음
- PG 응답 시간 지표를 대시보드에 추가

## Action Item

- 오세훈님: 커넥션 풀 분리 및 지표 추가
- 김도윤님: 타임아웃 시 `PENDING` 처리 경로 구현
""",
    },
    {
        "id": "DOC-082",
        "title": "승인 완료 후 주문 저장 실패 3건",
        "department": "백엔드개발팀",
        "type": "이슈 정리",
        "project": "Project Falcon",
        "date": "2025-12-09",
        "systems": ["ORBIT"],
        "features": ["결제", "주문"],
        "participants": [],
        "concepts": ["정합성", "외부 시스템 장애", "보상 처리"],
        "body": """## 현상

12월 8일 저녁, 카드 결제는 됐는데 주문이 생성되지 않았다는 문의가 3건 들어왔습니다. 카드사 승인 문자는 받았고 앱에는 주문이 없습니다.

## 재현

의도적으로 재현하지는 않았습니다. 로그로 경위를 확인했습니다.

## 로그 분석

```text
19:41:02 pg.approve.request orderId=90881 amount=52000
19:41:04 pg.approve.response result=SUCCESS txId=pg_20251208_8812
19:41:04 db.save.payment FAILED
   org.springframework.dao.CannotAcquireLockException:
   Deadlock found when trying to get lock
```

같은 시각 `order` 테이블에 데드락이 다수 발생했습니다. 재고 차감 배치와 주문 저장이 같은 행을 반대 순서로 잠갔습니다.

## 영향 범위

- 결제는 완료, 주문 미생성 3건 (총 14만 8천원)
- 같은 시간대 데드락으로 실패한 주문 요청 27건 (이쪽은 승인 전 실패라 문제 없음)

## 임시 대응

3건은 PG 관리자 화면에서 확인 후 수동 취소했습니다. 고객에게 개별 안내했고 재주문 시 사용할 쿠폰을 지급했습니다.

## 실제 원인

두 가지가 겹쳤습니다.

1. 재고 차감 배치와 주문 저장의 락 획득 순서가 달라 데드락 발생
2. PG 승인 이후 DB 저장 실패에 대한 보정 장치가 없음

1번은 즉시 원인이고, 2번은 이런 상황에서 자동 복구가 안 되는 구조적 문제입니다.

## 근본 해결

- 재고 차감 배치의 정렬 순서를 주문 저장과 동일하게 맞춤 (12월 9일 배포)
- Falcon 에서 설계 중인 `PENDING` 선기록 + 보정 배치가 적용되면 2번이 해결됩니다

## 재발 방지

Falcon 배포 전까지는 같은 상황이 재발할 수 있습니다. 임시로 PG 승인 성공 후 DB 저장이 실패하면 즉시 PG 취소를 시도하는 처리를 넣었습니다. 이 취소도 실패하면 알림을 보냅니다. 완전하지 않지만 수동 발견보다는 낫습니다.
""",
    },
    {
        "id": "DOC-083",
        "title": "결제 상태 전이 정의",
        "department": "백엔드개발팀",
        "type": "비즈니스 기능 설계",
        "project": "Project Falcon",
        "date": "2025-12-11",
        "systems": ["ORBIT"],
        "features": ["결제", "결제 취소", "부분 취소"],
        "participants": [],
        "concepts": ["상태 전이", "부분 취소", "Edge Case"],
        "body": """## 배경

상태값을 5개로 정리하면서 전이 규칙을 명확히 합니다. 지금은 어떤 상태에서 어떤 상태로 갈 수 있는지 코드를 봐야만 알 수 있습니다.

## 상태

| 상태 | 의미 |
| --- | --- |
| PENDING | 승인 요청했으나 결과 미확정 |
| APPROVED | 승인 완료 |
| FAILED | 승인 실패 또는 거절 |
| PARTIAL_CANCELED | 일부 취소됨 |
| CANCELED | 전액 취소됨 |

## 전이 규칙

```text
(신규)           -> PENDING
PENDING          -> APPROVED          (PG 승인 확인)
PENDING          -> FAILED            (PG 거절 확인)
APPROVED         -> PARTIAL_CANCELED  (부분 취소)
APPROVED         -> CANCELED          (전액 취소)
PARTIAL_CANCELED -> PARTIAL_CANCELED  (추가 부분 취소)
PARTIAL_CANCELED -> CANCELED          (잔액 전부 취소)
```

허용되지 않는 전이:

- `FAILED` 에서 다른 상태로 이동할 수 없습니다. 재시도는 새 결제 건입니다.
- `CANCELED` 에서 되돌릴 수 없습니다. 재결제는 새 건입니다.
- `PENDING` 에서 바로 취소할 수 없습니다. 결과 확정이 먼저입니다.

## PENDING 확정 규칙

보정 배치가 5분 이상 `PENDING` 인 건을 처리합니다.

1. PG 에 거래 조회
2. 승인됨 → `APPROVED` 로 갱신, 주문 생성
3. 승인 안 됨 → `FAILED`
4. PG 에서도 알 수 없음 → 그대로 두고 알림. 30분 경과 시 수동 확인 대상

## Edge Case

- **부분 취소 누적이 총액과 같아짐**: `canceled_amount == total_amount` 가 되면 `CANCELED` 로 전이합니다. `PARTIAL_CANCELED` 로 남겨두면 "전액 취소된 건"을 조회할 때 놓칩니다.
- **부분 취소 중 전액 취소 요청**: 남은 금액만 취소하고 `CANCELED` 로 갑니다. 이미 취소된 금액을 다시 취소하지 않습니다.
- **취소 요청이 PG 에서 실패**: 상태를 바꾸지 않고 `payment_history` 에만 실패를 기록합니다. 상태와 실제 PG 상태가 어긋나면 안 됩니다.
- **적립금만으로 결제된 주문**: PG 거래가 없습니다. `pg_transaction_id` 가 null 이고 취소 시 PG 호출을 건너뜁니다. 상태 전이는 동일합니다.

## 고민

`PENDING` 에서 오래 머무는 건을 자동으로 `FAILED` 처리할지 고민했습니다. PG 조회가 계속 실패하는 상황에서 임의로 실패 처리하면 실제로는 승인된 결제를 놓칩니다. 자동 처리하지 않고 알림 후 수동 확인으로 둡니다.
""",
    },
    {
        "id": "DOC-084",
        "title": "[리뷰] PaymentService transaction 범위",
        "department": "백엔드개발팀",
        "type": "코드리뷰",
        "project": "Project Falcon",
        "date": "2025-12-16",
        "systems": ["ORBIT", "MARS"],
        "features": ["결제"],
        "participants": ["김도윤", "노태윤"],
        "concepts": ["Transaction", "외부 호출", "Rollback"],
        "body": """## 현재 구현

```java
@Transactional
public PaymentResult approve(ApproveCommand command) {
    var payment = paymentRepository.save(Payment.pending(command));
    rewardClient.use(command.memberId(), command.pointAmount(), command.orderId());
    var pgResult = pgClient.approve(command.toPgRequest());
    payment.approve(pgResult);
    orderService.markPaid(command.orderId());
    return PaymentResult.from(payment);
}
```

## 리뷰 의견

노태윤님: 트랜잭션 안에서 외부 호출을 두 번 합니다. PG 응답이 15초까지 걸릴 수 있는데 그동안 DB 커넥션을 잡고 있습니다. 동시 결제가 몰리면 커넥션 풀이 마릅니다.

설계 문서에는 `PENDING` 을 먼저 커밋하고 PG 를 호출하도록 되어 있는데 구현은 한 트랜잭션입니다.

김도윤님: 맞습니다. 나누겠습니다. `PENDING` 저장을 별도 트랜잭션으로 커밋하고, PG 호출은 트랜잭션 밖에서, 결과 반영을 다시 별도 트랜잭션으로 처리합니다.

노태윤님: 그러면 적립금 사용 실패 시 `PENDING` 행이 남습니다. 이 경우 보정 배치가 PG 에 조회하는데 PG 호출 자체를 안 했으므로 조회 결과가 없습니다. 처리 경로가 있나요.

김도윤님: PG 조회 결과가 없으면 `FAILED` 로 확정하도록 하겠습니다. 다만 PG 장애로 조회가 안 되는 것과 애초에 요청하지 않은 것을 구분해야 합니다. `payment_history` 에 `APPROVE_REQUESTED` 가 없으면 요청 전 실패로 판단할 수 있습니다.

노태윤님: `nit:` 적립금 사용 실패 시 예외 메시지에 금액이 그대로 들어갑니다. 로그에는 남아도 되지만 클라이언트 응답에는 내부 금액 정보를 넣지 않는 편이 좋겠습니다.

## 수정 사항

- 트랜잭션 3단계 분리 (PENDING 커밋 / PG 호출 / 결과 반영)
- `payment_history` 에 `APPROVE_REQUESTED` 를 PG 호출 직전 기록
- 보정 배치에서 요청 전 실패 판별
- 예외 메시지 정리

## 남은 확인

적립금 사용과 PG 승인 순서를 바꾸는 게 나을지 논의가 남았습니다. PG 를 먼저 하면 적립금 실패 시 PG 취소가 필요해 더 복잡합니다. 현재 순서를 유지하되 적립금 사용 취소 실패에 대한 재시도 큐를 반드시 넣기로 했습니다.
""",
    },
    {
        "id": "DOC-085",
        "title": "결제 취소 API v2 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": "Project Falcon",
        "date": "2025-12-18",
        "systems": ["ORBIT"],
        "features": ["결제 취소", "부분 취소", "환불"],
        "participants": [],
        "concepts": ["부분 취소", "멱등성", "Error Code"],
        "body": """## Endpoint

```http
POST /payments/{paymentId}/cancel
Idempotency-Key: {clientGeneratedKey}
Authorization: Bearer {serviceToken}
```

전액 취소와 부분 취소를 같은 엔드포인트로 처리합니다.

## Request

```json
{
  "cancelAmount": 12000,
  "reason": "CUSTOMER_REQUEST",
  "orderItemIds": [551201]
}
```

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| cancelAmount | N | 미지정 시 잔여 전액 |
| reason | Y | CUSTOMER_REQUEST / OUT_OF_STOCK / SYSTEM / ABUSE |
| orderItemIds | N | 부분 취소 시 대상 상품 |

## Response

```json
{
  "paymentId": 771903,
  "status": "PARTIAL_CANCELED",
  "canceledAmount": 12000,
  "totalCanceledAmount": 12000,
  "remainingAmount": 22000,
  "pointRestored": 900,
  "canceledAt": "2025-12-18T10:05:12+09:00"
}
```

`pointRestored` 는 취소 금액 비율로 복원된 적립금입니다. 배분은 상품 금액 비율을 따르며 단수는 올림 처리합니다.

## 취소 순서

1. 취소 가능 금액 확인
2. PG 취소 요청
3. `payment` 상태 및 누적 취소액 갱신
4. 적립금 복원 및 적립 회수 (MARS)
5. 주문 상태 갱신

PG 취소가 실패하면 3~5는 진행하지 않습니다.

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| PAY_2001 | 404 | 결제 건 없음 |
| PAY_2002 | 409 | 취소 불가 상태 (PENDING, FAILED) |
| PAY_2003 | 400 | 취소 금액이 잔여 금액 초과 |
| PAY_2004 | 409 | 이미 전액 취소됨 |
| PAY_2005 | 502 | PG 취소 실패 |
| PAY_2006 | 409 | 취소 가능 기간 경과 |

## 주의사항

`PAY_2005` 는 우리 상태를 바꾸지 않습니다. 재시도 가능하며 같은 멱등키를 쓰면 중복 취소가 나지 않습니다.

취소 가능 기간은 결제일로부터 365일입니다. PG 정책이므로 이후에는 환불 계좌를 받아 수동 처리해야 합니다.
""",
    },
    {
        "id": "DOC-086",
        "title": "Falcon Sprint 1 진행 현황",
        "department": "프로젝트관리팀",
        "type": "프로젝트 진행상황",
        "project": "Project Falcon",
        "date": "2025-12-19",
        "systems": ["ORBIT"],
        "features": ["결제"],
        "participants": [],
        "concepts": ["일정", "Blocker"],
        "body": """## 스프린트 개요

12월 8일 ~ 12월 19일. 결제 승인 v2 의 뼈대를 만드는 스프린트입니다.

## 완료

- 결제 상태 전이 정의
- `payment` / `payment_history` 스키마 확정
- 승인 API v2 초안
- 취소 API v2 명세
- PG 타임아웃 조정 및 커넥션 풀 분리

## 진행 중

- 승인 처리 구현 (트랜잭션 분리 리뷰 반영 중)
- 보정 배치 설계

## 대기

- 부분 취소 구현
- 적립금 연동 인터페이스 확정

## Blocker

적립금 사용을 결제 요청에 포함시키는 방식이 MARS 팀과 합의되지 않았습니다. 인터페이스가 정해져야 승인 구현을 마무리할 수 있습니다. 12월 22일 협의 예정입니다.

부분 취소 시 적립금 배분 정책은 Nova 에서 넘어온 미확정 항목입니다. 10월 기획 회의에서 금액 비율 배분으로 정해졌지만 적립 회수 기준이 남아 있습니다.

## Risk

12월 8일 발생한 승인 후 저장 실패 이슈가 Falcon 배포 전까지 재발할 수 있습니다. 임시 조치를 넣었으나 완전하지 않습니다.

연말 트래픽 기간에는 배포가 제한되므로 실질적인 개발 기간이 짧습니다.

## 다음 스프린트

- 승인 구현 완료 및 스테이징 배포
- 보정 배치 구현
- 부분 취소 착수
""",
    },
    {
        "id": "DOC-087",
        "title": "2025 배포 규칙",
        "department": "DevOps팀",
        "type": "팀 Ground Rule",
        "project": None,
        "date": "2025-12-22",
        "systems": [],
        "features": [],
        "participants": [],
        "concepts": ["배포", "팀 규칙", "롤백"],
        "body": """## 배포 시간

- 평일 10:00 ~ 16:00
- 금요일 오후 배포 금지
- 연말(12/24 ~ 1/2) 트래픽 기간 배포 동결. 장애 대응 핫픽스만 허용

## 배포 전 확인

- [ ] PR 이 머지되어 있고 Approve 조건을 충족했는가
- [ ] 스테이징에서 동작을 확인했는가
- [ ] DB 스키마 변경이 있다면 선행 적용되었는가
- [ ] 롤백 방법을 정리했는가
- [ ] 관련 팀에 공유했는가

스키마 변경과 이를 사용하는 코드는 같은 배포에 넣지 않습니다. 스키마를 먼저 적용하고 다음 배포에서 코드를 올립니다.

## 배포 후 확인

배포 후 15분간 다음을 봅니다.

- 오류율
- 응답 시간 p99
- 주요 API 성공률

이상이 있으면 원인 분석보다 롤백을 먼저 합니다.

## 롤백

- 배포 담당자가 판단하고 즉시 실행합니다. 승인 절차 없습니다
- 롤백 후 상황을 공유 채널에 남깁니다
- 스키마가 이미 변경된 경우 코드만 되돌려도 동작하도록 설계해야 합니다

## 핫픽스

- 장애 대응 목적에 한합니다
- 배포 동결 기간에도 가능하지만 리드 확인이 필요합니다
- 핫픽스 후 다음 영업일에 정식 PR 로 정리합니다

## 기록

모든 배포는 배포 이력에 남깁니다. 배포자, 시각, 내용, 롤백 여부를 포함합니다.
""",
    },
    {
        "id": "DOC-088",
        "title": "결제 로그 조회 방법",
        "department": "백엔드개발팀",
        "type": "매뉴얼 / 정의서",
        "project": None,
        "date": "2025-12-23",
        "systems": ["ORBIT"],
        "features": ["결제"],
        "participants": [],
        "concepts": ["운영 대응", "로그", "추적"],
        "body": """## 어디서 보나

| 대상 | 위치 |
| --- | --- |
| 애플리케이션 로그 | 로그 대시보드 (ORBIT 인덱스) |
| PG 요청/응답 | `payment_history.pg_response` |
| 결제 상태 변화 | `payment_history` |
| 사용자 화면 오류 | 프론트 오류 수집 도구 |

## 문의 대응 순서

주문번호부터 시작하는 것이 가장 빠릅니다.

```sql
SELECT id, status, total_amount, canceled_amount, pg_transaction_id, created_at
FROM payment
WHERE order_id = :orderId;
```

그다음 이력을 봅니다.

```sql
SELECT event_type, amount, created_at
FROM payment_history
WHERE payment_id = :paymentId
ORDER BY created_at;
```

이력에 `APPROVE_REQUESTED` 는 있는데 `APPROVED` 나 `APPROVE_FAILED` 가 없으면 PG 응답을 못 받은 것입니다. PG 관리자 화면에서 거래번호로 실제 상태를 확인하세요.

## 로그 검색 키

- `orderId` 로 주문 전체 흐름
- `paymentId` 로 결제 건
- `idempotencyKey` 로 중복 요청 확인
- `pgTransactionId` 로 PG 대조

중복 방지 키로 검색하면 클라이언트가 몇 번 요청했는지 볼 수 있습니다. 중복 결제 문의에서 유용합니다.

## 주의

- `pg_response` 에는 마스킹된 값이 들어갑니다. 원문 카드번호는 어디에도 저장하지 않습니다
- 로그를 캡처해 외부에 공유하지 마세요. 주문번호와 회원 ID 가 함께 노출됩니다
- 운영 DB 에 직접 접속할 때는 조회 전용 계정을 사용하세요. 접근 절차는 별도 문서에 있습니다
""",
    },
    {
        "id": "DOC-089",
        "title": "연말 트래픽 구간 결제 응답 지연",
        "department": "DevOps팀",
        "type": "이슈 정리",
        "project": "Project Falcon",
        "date": "2025-12-29",
        "systems": ["ORBIT"],
        "features": ["결제"],
        "participants": ["오세훈"],
        "concepts": ["부하", "커넥션 풀", "Timeout"],
        "body": """## 현상

12월 28일 20시부터 22시까지 결제 승인 API p99 응답 시간이 1.4초에서 11초로 올랐습니다. 타임아웃으로 실패한 요청이 340건입니다.

## 지표

| 시각 | 요청/분 | p99 | 오류율 |
| --- | --- | --- | --- |
| 19:00 | 820 | 1.4s | 0.2% |
| 20:30 | 2,140 | 6.8s | 1.1% |
| 21:15 | 2,980 | 11.2s | 3.4% |
| 22:30 | 1,110 | 1.6s | 0.3% |

## 분석

PG 응답 시간은 평소와 같았습니다. 병목은 우리 쪽이었습니다.

DB 커넥션 풀 대기가 급증했고, 활성 커넥션이 상한(50)에 붙어 있었습니다. 12월 초에 PG 전용 커넥션 풀을 분리했지만 DB 풀은 그대로였습니다.

느린 쿼리를 보니 주문 목록 조회가 눈에 띕니다.

```sql
SELECT ... FROM orders o
JOIN order_item oi ON oi.order_id = o.id
WHERE o.member_id = :memberId
ORDER BY o.created_at DESC
LIMIT 20;
```

`order_item` 조인 때문에 대량 주문 회원에서 느려집니다. 평소에는 문제없다가 트래픽이 몰리면 커넥션을 오래 잡습니다.

## 임시 대응

- DB 커넥션 풀을 50에서 80으로 증설 (12월 28일 22시)
- 주문 목록 조회에 캐시 30초 적용

## 근본 해결

- 주문 목록 조회를 두 단계로 분리해 조인 제거
- 결제 경로와 조회 경로의 커넥션 풀 분리 검토

조회 분리는 Falcon 범위 밖이라 별도 과제로 등록했습니다.

## 남은 의문

커넥션 풀 증설로 지표는 회복됐지만 근본 원인인 조회 쿼리는 그대로입니다. 다음 대형 트래픽 때 다시 나올 가능성이 높습니다. 1월 중 조회 개선을 우선순위에 넣어야 합니다.
""",
    },
    {
        "id": "DOC-090",
        "title": "주문 취소 가능 시간 정책 논의",
        "department": "서비스기획팀",
        "type": "기획자와의 회의록",
        "project": "Project Falcon",
        "date": "2026-01-06",
        "systems": ["ORBIT"],
        "features": ["주문 취소", "결제 취소"],
        "participants": ["강민재", "노태윤"],
        "concepts": ["정책", "배송", "기술적 제약"],
        "body": """## 논의 배경

현재 주문 취소는 "배송 준비 중"까지만 가능합니다. 상품 준비가 시작되면 앱에서 취소가 막히고 고객지원팀을 통해야 합니다. 이 단계 전환이 판매자마다 제각각이라 어떤 주문은 5분 만에 취소가 막힙니다.

## 현황

| 상태 | 앱 취소 | 평균 도달 시간 |
| --- | --- | --- |
| 결제 완료 | 가능 | - |
| 상품 준비 중 | 불가 | 주문 후 평균 3시간, 최소 4분 |
| 배송 중 | 불가 | |
| 배송 완료 | 반품 절차 | |

## 기획 요청

강민재님 요청은 "결제 후 30분은 무조건 취소 가능"입니다. 실수 주문의 대부분이 30분 안에 인지된다는 데이터가 있습니다.

## 기술적 제약

노태윤님이 확인한 내용입니다. 판매자가 이미 상품 준비를 시작했다면 우리가 일방적으로 취소할 수 없습니다. 판매자 시스템에 취소 요청을 보내고 수락을 받아야 합니다.

일부 판매자는 이 연동이 없어서 수동으로 처리합니다. 30분 무조건 취소를 보장하려면 연동이 없는 판매자를 어떻게 할지 정해야 합니다.

## 검토한 방안

1. 전 판매자 대상 30분 취소 보장 — 연동 없는 판매자는 CS 수동 처리
2. 연동된 판매자만 30분 보장, 나머지는 현행 유지
3. 결제 후 30분간 판매자에게 주문을 넘기지 않음

3안은 배송이 그만큼 늦어져 판매자 반발이 예상됩니다.

## 결정 사항

- 2안으로 시작합니다. 연동된 판매자부터 적용합니다
- 앱에서 취소 가능 여부를 명확히 표시합니다

## 미확정

연동 없는 판매자 비중이 얼마나 되는지 확인이 필요합니다. 비중이 크면 사용자 경험이 판매자마다 달라져 혼란이 생깁니다. 데이터 확인 후 적용 범위를 다시 봅니다.
""",
    },
]
