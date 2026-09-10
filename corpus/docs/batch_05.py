"""DOC-061 ~ DOC-075 : Project Nova 후반 - ledger 구조 확정, 쿠폰, 마이그레이션."""

DOCUMENTS = [
    {
        "id": "DOC-061",
        "title": "reward_balance / reward_history 분리 설계",
        "department": "백엔드개발팀",
        "type": "DB 테이블 설계안",
        "project": "Project Nova",
        "date": "2025-09-02",
        "systems": ["MARS"],
        "features": ["포인트", "리워드 지급"],
        "participants": [],
        "concepts": ["ledger", "낙관적 락", "정합성", "Index"],
        "body": """## 배경

기존 `reward` 단일 테이블 설계는 동시 사용 시 잔액이 음수가 되는 문제로 폐기했습니다. 이력을 진실의 원천으로 두고 조회용 잔액을 따로 관리하는 구조로 바꿉니다.

## reward_history

모든 적립금 변동을 기록합니다. 이 테이블만으로 잔액을 복원할 수 있어야 합니다.

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| id | BIGINT | PK | |
| member_id | BIGINT | NOT NULL | |
| type | VARCHAR(16) | NOT NULL | GRANT / USE / REVOKE / EXPIRE |
| amount | INT | NOT NULL | 양수. 방향은 type 이 결정 |
| remaining_amount | INT | NULL | GRANT 에만 사용. 미사용 잔여 |
| reason | VARCHAR(32) | NOT NULL | ORDER_REWARD 등 |
| reference_key | VARCHAR(100) | NOT NULL | 중복 방지 키 |
| source_history_id | BIGINT | NULL | USE/EXPIRE 가 차감한 GRANT 행 |
| expires_at | DATETIME | NULL | GRANT 만료일 |
| created_at | DATETIME | NOT NULL | |

```sql
CREATE UNIQUE INDEX uk_history_ref ON reward_history (type, reason, reference_key);
CREATE INDEX idx_history_member ON reward_history (member_id, created_at DESC);
CREATE INDEX idx_history_grant_usable
  ON reward_history (member_id, type, remaining_amount, expires_at);
```

## reward_balance

조회 전용 스냅샷입니다.

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| member_id | BIGINT | PK | |
| balance | INT | NOT NULL | |
| version | BIGINT | NOT NULL | 낙관적 락 |
| updated_at | DATETIME | NOT NULL | |

## 갱신 방식

이력 삽입과 잔액 갱신을 같은 트랜잭션에서 처리합니다. 잔액은 버전 조건으로 갱신합니다.

```sql
UPDATE reward_balance
SET balance = :newBalance, version = version + 1, updated_at = NOW()
WHERE member_id = :memberId AND version = :expectedVersion;
```

영향 행이 0이면 다른 트랜잭션이 먼저 갱신한 것이므로 재조회 후 재시도합니다. 3회까지 시도하고 실패하면 오류를 반환합니다.

## 왜 잔액 테이블을 남겼는가

완전 ledger 로 가면 조회 때마다 이력 합계를 계산해야 합니다. 이력이 수만 건인 계정이 이미 존재하고, 잔액 조회는 상품 목록부터 주문서까지 거의 모든 화면에서 호출됩니다. 조회 비용을 감당할 수 없다고 판단했습니다.

## 왜 락 범위가 줄어드는가

이전 설계는 사용 가능한 GRANT 행 전체를 `FOR UPDATE` 로 잠갔습니다. 지금은 잔액 행 하나에만 버전 조건이 걸리고, GRANT 행 차감은 같은 트랜잭션 안에서 순서대로 진행됩니다. 충돌하면 통째로 재시도하므로 부분 반영이 남지 않습니다.

## 정합성 대조

일 1회 배치로 `reward_history` 합계와 `reward_balance.balance` 를 비교합니다. 차이가 있으면 알림을 보내고 자동으로 고치지 않습니다. 자동 보정은 원인을 덮어버립니다.

## 남은 고민

`remaining_amount` 를 GRANT 행에 두면서 이력 테이블이 불변이 아니게 됐습니다. 순수 ledger 라면 차감도 새 행으로 남겨야 합니다. 조회 편의를 위해 타협했고, 대신 USE 행에 `source_history_id` 를 남겨 추적은 가능하게 했습니다.
""",
    },
    {
        "id": "DOC-062",
        "title": "적립금 사용 및 차감 순서 설계",
        "department": "백엔드개발팀",
        "type": "비즈니스 기능 설계",
        "project": "Project Nova",
        "date": "2025-09-05",
        "systems": ["MARS", "ORBIT"],
        "features": ["포인트", "주문"],
        "participants": [],
        "concepts": ["차감 순서", "동시성", "Edge Case", "재시도"],
        "body": """## 배경

적립금 사용은 결제 흐름 안에 들어갑니다. 실패하면 주문 전체가 막히므로 예외 상황 정의가 중요합니다.

## 차감 순서

1. 만료일이 가까운 GRANT 행부터
2. 만료일이 같으면 먼저 적립된 행부터
3. 만료일 없는 행은 마지막

```sql
ORDER BY expires_at IS NULL, expires_at ASC, id ASC
```

## 정상 Flow

1. 사용 요청 수신 (회원, 금액, 참조키)
2. 참조키로 중복 확인
3. 사용 가능한 GRANT 행 조회
4. 순서대로 `remaining_amount` 차감하며 USE 이력 생성
5. `reward_balance` 를 낙관적 락으로 갱신
6. 커밋

## Edge Case

- **잔액 부족**: 3단계에서 필요한 금액을 채우지 못하면 `REWARD_2003` 을 반환합니다. 부분 사용은 하지 않습니다.
- **동시 사용 요청**: 5단계에서 버전 충돌이 나면 처음부터 재시도합니다. 재조회하면 잔액이 줄어 있어 두 번째 요청이 잔액 부족으로 정상 거부됩니다.
- **차감 중 만료**: 3단계 조회 시점에는 유효했는데 커밋 전에 만료되는 경우가 있습니다. 만료 배치도 같은 행을 건드리므로 버전 충돌로 잡힙니다.
- **결제 실패 후 사용 취소**: ORBIT 이 결제에 실패하면 적립금을 되돌려야 합니다. 회수가 아니라 "사용 취소"로, 차감했던 GRANT 행의 `remaining_amount` 를 복원합니다. 만료일도 원래대로 유지됩니다.
- **사용 취소 시점에 이미 만료**: 만료된 GRANT 행에 복원하면 쓸 수 없는 적립금이 됩니다. 이 경우 만료일을 취소 시점 기준 7일로 연장합니다. 사용자에게 불리하지 않게 하기 위한 예외입니다.
- **중복 사용 요청**: 참조키가 같으면 기존 결과를 반환합니다. 주문번호를 참조키로 쓰므로 결제 재시도에서 이중 차감이 나지 않습니다.

## 재시도 정책

버전 충돌 재시도는 3회, 간격은 즉시입니다. 백오프를 넣으면 결제 응답이 느려집니다. 3회 실패는 같은 회원에 동시 요청이 몰린 비정상 상황으로 보고 `REWARD_2009` 를 반환합니다.

## 고민

사용 취소 시 만료일 연장은 정책적으로 애매합니다. 원래 만료됐어야 할 적립금이 되살아납니다. 기획팀에 확인을 요청했고 아직 회신 전이라 개발 판단으로 넣어둔 상태입니다.
""",
    },
    {
        "id": "DOC-063",
        "title": "[리뷰] 잔액 갱신 낙관적 락 재시도",
        "department": "백엔드개발팀",
        "type": "코드리뷰",
        "project": "Project Nova",
        "date": "2025-09-09",
        "systems": ["MARS"],
        "features": ["포인트"],
        "participants": ["박준호", "김도윤"],
        "concepts": ["Transaction", "재시도", "동시성"],
        "body": """## 현재 구현

재시도를 서비스 메서드 안에서 루프로 처리했습니다.

```java
@Transactional
public UseResult use(UseCommand command) {
    for (int attempt = 0; attempt < 3; attempt++) {
        var balance = balanceRepository.find(command.memberId());
        ...
        int updated = balanceRepository.updateWithVersion(balance);
        if (updated == 1) return result;
    }
    throw new RewardConcurrencyException();
}
```

## 리뷰 의견

김도윤님: 트랜잭션 안에서 재시도하면 의미가 없습니다. 첫 시도에서 만든 이력 삽입이 롤백되지 않은 채 두 번째 시도가 돌고, 커밋 시점에 중복 이력이 들어갑니다. 재시도는 트랜잭션 밖에서 감싸야 합니다.

박준호님: 확인했습니다. `@Transactional` 을 안쪽 메서드에 두고 바깥에서 재시도하는 구조로 바꾸겠습니다.

김도윤님: 같은 클래스 안에서 호출하면 프록시를 안 타서 트랜잭션이 안 걸립니다. 재시도 담당을 별도 컴포넌트로 분리하거나 `TransactionTemplate` 을 직접 쓰는 편이 안전합니다.

김도윤님: 예외 타입도 봐야 합니다. 지금은 영향 행 0 만 재시도 대상인데, unique 위반이나 데드락도 재시도 가능한 경우가 있습니다. 반대로 잔액 부족은 재시도해도 결과가 같으니 즉시 실패시켜야 합니다.

박준호님: 재시도 대상 예외를 명시적으로 열거하겠습니다.

## 수정 사항

- 재시도를 `RewardRetryExecutor` 로 분리, 각 시도가 독립 트랜잭션
- 재시도 대상: 버전 충돌, 데드락
- 즉시 실패: 잔액 부족, 잘못된 요청, 중복 참조키

## 남은 확인

재시도 횟수와 실패 시 응답 코드는 그대로 두되, 운영에서 3회 실패가 실제로 발생하는지 지표를 남기기로 했습니다. 지표를 보고 횟수를 조정합니다.
""",
    },
    {
        "id": "DOC-064",
        "title": "적립금 사용 API 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": "Project Nova",
        "date": "2025-09-12",
        "systems": ["MARS", "ORBIT"],
        "features": ["포인트", "주문"],
        "participants": [],
        "concepts": ["멱등성", "Error Code", "취소"],
        "body": """## Endpoint

```http
POST /rewards/use
Authorization: Bearer {serviceToken}
```

## Request

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| memberId | number | Y | |
| amount | number | Y | 1 이상 |
| referenceKey | string | Y | 주문번호 권장 |
| memo | string | N | |

```json
{
  "memberId": 1024,
  "amount": 2500,
  "referenceKey": "ORD-2025-0912-0114"
}
```

## Response

```json
{
  "useId": 771201,
  "memberId": 1024,
  "used": 2500,
  "balance": 9900,
  "details": [
    { "historyId": 660013, "amount": 1500, "expiresAt": "2025-10-01T00:00:00+09:00" },
    { "historyId": 661882, "amount": 1000, "expiresAt": "2026-03-14T00:00:00+09:00" }
  ],
  "duplicated": false
}
```

`details` 는 어느 적립분에서 얼마씩 차감했는지 보여줍니다. 사용 취소 시 필요하지 않지만 정산과 문의 대응에 씁니다.

## 사용 취소

```http
POST /rewards/use/{useId}/cancel
```

결제 실패 시 호출합니다. 차감했던 적립분을 복원합니다. 이미 만료된 적립분은 취소 시점 기준 7일 만료로 복원됩니다.

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| REWARD_2001 | 400 | 금액 오류 |
| REWARD_2003 | 409 | 잔액 부족 |
| REWARD_2005 | 409 | 사용 불가 회원 상태 |
| REWARD_2007 | 404 | 취소 대상 사용 건 없음 |
| REWARD_2008 | 409 | 이미 취소된 사용 건 |
| REWARD_2009 | 503 | 동시 요청 충돌로 처리 실패. 재시도 가능 |

## 주의사항

`REWARD_2009` 는 재시도하면 성공할 수 있습니다. 클라이언트가 같은 `referenceKey` 로 재호출하면 되고, 중복 차감은 발생하지 않습니다.

`REWARD_2003` 은 재시도해도 같습니다. 사용자에게 잔액 부족을 안내하세요.
""",
    },
    {
        "id": "DOC-065",
        "title": "리워드 회수 API 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": "Project Nova",
        "date": "2025-09-16",
        "systems": ["MARS"],
        "features": ["리워드 회수"],
        "participants": [],
        "concepts": ["회수", "음수 잔액", "승인"],
        "body": """## Endpoint

```http
POST /rewards/revoke
Authorization: Bearer {serviceToken}
```

지급했던 적립금을 되돌립니다. 사용 취소와 다릅니다. 사용 취소는 쓴 적립금을 돌려주는 것이고, 회수는 준 적립금을 빼앗는 것입니다.

## Request

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| grantReferenceKey | string | Y | 회수할 지급 건의 참조키 |
| reason | string | Y | ORDER_CANCELED / ABUSE / MISTAKE |
| amount | number | N | 미지정 시 전액 |

부분 회수는 `amount` 를 지정합니다. 주문 부분 취소에서 사용합니다.

## Response

```json
{
  "revokeId": 90112,
  "revoked": 3000,
  "balance": 6900,
  "unrecoverable": 0
}
```

`unrecoverable` 은 이미 사용되어 회수하지 못한 금액입니다.

## 회수 규칙

| 상황 | 처리 |
| --- | --- |
| 지급분이 그대로 남아 있음 | 전액 회수 |
| 일부 사용됨 | 남은 만큼만 회수, 나머지는 `unrecoverable` |
| 전액 사용됨 | 회수 0, `unrecoverable` 에 전액 |
| 지급분이 만료됨 | 회수 0, 이력만 기록 |

잔액이 음수가 되는 회수는 하지 않습니다. 정책상 사용된 적립금은 회수하지 않기로 했습니다.

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| REWARD_3001 | 404 | 지급 이력 없음 |
| REWARD_3002 | 409 | 이미 회수된 지급 건 |
| REWARD_3003 | 400 | 회수 금액이 지급액 초과 |

## 주의사항

`unrecoverable` 이 0 이 아니면 회수가 완전하지 않다는 뜻입니다. 어뷰징 대응에서는 이 값을 별도로 집계해 손실을 파악해야 합니다.

운영자 수동 회수는 이 API 를 직접 호출하지 않고 AIMS 화면을 사용하세요. 승인 절차가 화면에만 붙어 있습니다.
""",
    },
    {
        "id": "DOC-066",
        "title": "주문 취소 시 리워드 회수 처리 설계",
        "department": "백엔드개발팀",
        "type": "비즈니스 기능 설계",
        "project": "Project Nova",
        "date": "2025-09-19",
        "systems": ["MARS", "ORBIT"],
        "features": ["리워드 회수", "주문 취소", "결제 취소"],
        "participants": [],
        "concepts": ["보상 처리", "Edge Case", "정합성", "부분 취소"],
        "body": """## 배경

주문이 취소되면 두 가지를 되돌려야 합니다. 주문으로 적립된 적립금은 회수하고, 주문에 사용한 적립금은 돌려줍니다. 방향이 반대라 헷갈리기 쉽습니다.

## 처리 대상

| 구분 | 처리 | API |
| --- | --- | --- |
| 주문 시 사용한 적립금 | 복원 | 사용 취소 |
| 주문으로 적립된 적립금 | 회수 | 회수 |

## 전체 취소 Flow

1. ORBIT 이 주문 취소 확정
2. 결제 취소 완료 확인
3. MARS 에 사용 취소 요청 (`ORD-...` 참조키)
4. MARS 에 적립 회수 요청 (`ORD-...` 참조키)
5. 두 결과를 주문 이력에 기록

3번과 4번은 별개 호출입니다. 하나만 성공하는 상황이 생길 수 있습니다.

## Edge Case

- **사용 취소는 성공, 회수는 실패**: 사용자에게 유리한 쪽만 처리된 상태입니다. 재시도 큐에 넣고 반복 시도합니다. 참조키가 있어 중복 처리는 안 됩니다.
- **회수 대상 적립금을 이미 사용**: `unrecoverable` 이 발생합니다. 정책상 추가 회수하지 않으므로 손실로 집계만 합니다.
- **적립 전 취소**: 배송 완료 시점 적립이므로 배송 전 취소는 적립 자체가 없습니다. 회수 요청 시 `REWARD_3001` 이 나오는데 이것을 실패로 보면 안 됩니다. ORBIT 에서 정상으로 처리합니다.
- **부분 취소**: 취소 금액 비율만큼 적립분을 회수합니다. 사용 적립금은 취소된 상품 금액에 배분된 만큼 복원합니다. 배분 기준이 정해지지 않아 현재는 전체 취소만 지원합니다.
- **취소 후 재주문**: 새 주문이므로 참조키가 다릅니다. 간섭 없습니다.

## 부분 취소 미해결

부분 취소에서 사용 적립금을 어떻게 나눌지가 정리되지 않았습니다. 3개 상품에 5,000점을 쓰고 1개를 취소하면 얼마를 돌려줄지 기준이 필요합니다. 상품 금액 비율로 나누는 안이 유력하지만 단수 처리가 남습니다.

기획팀에 정책 요청을 등록했고 10월 회의에서 다룹니다. 그때까지 부분 취소 주문은 적립금 사용이 불가능하도록 ORBIT 에서 막고 있습니다.
""",
    },
    {
        "id": "DOC-067",
        "title": "대사 배치에서 잔액 불일치 12건 확인",
        "department": "백엔드개발팀",
        "type": "이슈 정리",
        "project": "Project Nova",
        "date": "2025-09-25",
        "systems": ["MARS"],
        "features": ["포인트"],
        "participants": [],
        "concepts": ["정합성", "대사", "배치"],
        "body": """## 현상

9월 24일 대사 배치에서 `reward_history` 합계와 `reward_balance.balance` 가 다른 회원이 12명 나왔습니다. 차이는 100점에서 4,500점 사이입니다.

## 분석

12건 중 11건이 같은 패턴입니다.

```text
history 합계 = 8,200
balance      = 6,700
차이         = 1,500 (balance 가 작음)
```

해당 회원의 이력을 보면 USE 이력이 있는데 대응하는 GRANT 차감이 반영되지 않았습니다. 시각을 보니 모두 9월 22일 14시대에 몰려 있습니다.

이 시간에 배포가 있었습니다. 배포 중 인스턴스가 종료되면서 트랜잭션이 중단된 것으로 보입니다. 다만 트랜잭션이 중단되면 전부 롤백되어야 하는데 일부만 반영된 것이 이상합니다.

확인 결과 사용 처리에서 GRANT 행 차감을 별도 메서드로 뺐고, 이 메서드에 `REQUIRES_NEW` 가 붙어 있었습니다. 리팩터링 과정에서 들어간 것으로 보이며 의도한 것이 아닙니다. 바깥 트랜잭션이 롤백되어도 안쪽은 이미 커밋된 상태가 됩니다.

나머지 1건은 운영자가 AIMS 에서 수동 지급 후 실패 화면을 보고 다시 지급한 케이스로, 실제로는 두 번 다 성공했습니다. 참조키가 다르게 생성되어 중복 방지에 걸리지 않았습니다.

## 임시 대응

12건은 이력을 근거로 잔액을 수동 보정했습니다. 보정 내역은 별도 기록으로 남겼습니다.

## 근본 해결

- `REQUIRES_NEW` 제거 (배포 완료)
- AIMS 수동 지급의 참조키를 화면 세션 기준으로 생성해 재시도 시 동일하게 유지

## 재발 방지

대사 배치가 이 문제를 잡아낸 것은 설계대로 동작한 것입니다. 다만 발견까지 이틀 걸렸습니다. 배치 주기를 하루 한 번에서 6시간마다로 줄이는 안을 검토합니다. 아직 적용하지 않았습니다.
""",
    },
    {
        "id": "DOC-068",
        "title": "쿠폰 구조 개선 범위 논의",
        "department": "백엔드개발팀",
        "type": "개발자 회의록",
        "project": "Project Nova",
        "date": "2025-10-02",
        "systems": ["MARS"],
        "features": ["쿠폰 지급", "쿠폰 사용"],
        "participants": ["박준호", "강민재"],
        "concepts": ["범위 산정", "발급 방식"],
        "body": """## 논의 배경

Nova 범위에서 쿠폰은 제외했는데, 적립금 구조가 바뀌면서 쿠폰만 옛 방식으로 남게 됐습니다. 최소한의 정리를 할지 논의했습니다.

## 현재 구조

쿠폰은 발급 시점에 회원별 행을 만듭니다. 대량 발급 이벤트에서 수십만 행이 한 번에 생성되어 배치가 오래 걸립니다.

| 문제 | 내용 |
| --- | --- |
| 대량 발급 지연 | 30만 건 발급에 25분 |
| 중복 발급 방지 없음 | 재실행 시 중복 |
| 사용 조건이 코드에 하드코딩 | 신규 조건 추가 시 배포 필요 |

## 강민재님 요청

- 발급 대상 조건을 운영에서 바꿀 수 있으면 좋겠다
- 특정 상품 카테고리 한정 쿠폰이 필요하다
- 선착순 쿠폰도 계획 중이다

## 개발 판단

박준호님은 선착순까지 넣으면 Nova 일정에 들어가지 않는다고 봤습니다. 현재 마이그레이션이 남아 있어 여력이 없습니다.

## 결정 사항

이번 범위:

- 발급 중복 방지 (참조키 도입)
- 발급 배치 성능 개선

다음 과제:

- 사용 조건 데이터화
- 선착순 발급

## 추후 논의

선착순은 동시성 문제가 적립금보다 까다롭습니다. 수량이 정해진 자원을 여러 명이 동시에 가져가는 구조라 별도 설계가 필요합니다. 지금 구조에 얹지 말고 처음부터 다시 보는 게 맞다고 정리했습니다.
""",
    },
    {
        "id": "DOC-069",
        "title": "coupon / coupon_issue 테이블 구조",
        "department": "백엔드개발팀",
        "type": "DB 테이블 설계안",
        "project": "Project Nova",
        "date": "2025-10-07",
        "systems": ["MARS"],
        "features": ["쿠폰 지급", "쿠폰 사용"],
        "participants": [],
        "concepts": ["테이블 분리", "Unique Constraint", "대량 발급"],
        "body": """## 배경

쿠폰 정의와 발급분을 분리해서 관리합니다. 기존에는 발급 행마다 할인 조건이 복사되어 있어 조건을 고치려면 모든 행을 갱신해야 했습니다.

## coupon (쿠폰 정의)

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | BIGINT | PK |
| code | VARCHAR(32) | 운영용 식별 코드, UNIQUE |
| name | VARCHAR(100) | 사용자 노출 이름 |
| discount_type | VARCHAR(16) | AMOUNT / RATE |
| discount_value | INT | 금액 또는 비율 |
| min_order_amount | INT | 최소 주문 금액 |
| max_discount_amount | INT | 정률 할인 시 상한 |
| valid_days | INT | 발급일로부터 유효일수 |
| created_at | DATETIME | |

## coupon_issue (발급분)

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | BIGINT | PK |
| coupon_id | BIGINT | FK -> coupon.id |
| member_id | BIGINT | |
| reference_key | VARCHAR(100) | 중복 발급 방지 |
| status | VARCHAR(16) | ISSUED / USED / EXPIRED / REVOKED |
| issued_at | DATETIME | |
| expires_at | DATETIME | 발급 시점에 계산해 고정 |
| used_at | DATETIME | |
| order_id | BIGINT | 사용된 주문 |

```sql
CREATE UNIQUE INDEX uk_issue_ref ON coupon_issue (coupon_id, reference_key);
CREATE INDEX idx_issue_member ON coupon_issue (member_id, status, expires_at);
CREATE INDEX idx_issue_expire ON coupon_issue (status, expires_at);
```

## 설계 고민

**expires_at 을 저장하는 이유**: `coupon.valid_days` 로 계산할 수 있지만, 쿠폰 정의의 유효일수를 나중에 바꾸면 이미 발급된 쿠폰의 만료일이 소급 변경됩니다. 발급 시점에 확정해 저장합니다.

**status 를 두는 이유**: 사용 여부를 `used_at IS NULL` 로 판단하면 만료와 회수를 구분할 수 없습니다. 상태를 명시적으로 둡니다.

**FK 를 coupon 에만 거는 이유**: 회원은 탈퇴 시 삭제되므로 FK 를 걸면 발급 이력이 함께 지워집니다. 쿠폰 정의는 삭제하지 않으므로 FK 를 유지합니다.

## 대량 발급

30만 건 발급 시 한 건씩 INSERT 하면 25분이 걸렸습니다. 1,000건 단위 배치 INSERT 로 바꾸면 2분대로 줄어듭니다. `reference_key` unique 제약이 있어 재실행해도 중복이 생기지 않습니다.

다만 배치 INSERT 에서 unique 위반이 하나라도 나면 해당 묶음 전체가 실패합니다. `INSERT IGNORE` 를 쓸지 묶음 크기를 줄일지 검토가 필요합니다.
""",
    },
    {
        "id": "DOC-070",
        "title": "쿠폰 발급 API 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": "Project Nova",
        "date": "2025-10-10",
        "systems": ["MARS"],
        "features": ["쿠폰 지급"],
        "participants": [],
        "concepts": ["멱등성", "대량 발급", "Error Code"],
        "body": """## 단건 발급

```http
POST /coupons/issue
Authorization: Bearer {serviceToken}
```

```json
{
  "couponCode": "WELCOME-2025",
  "memberId": 1024,
  "referenceKey": "SIGNUP:1024"
}
```

응답:

```json
{
  "issueId": 5512099,
  "couponCode": "WELCOME-2025",
  "name": "신규 가입 3천원 할인",
  "expiresAt": "2025-11-09T23:59:59+09:00",
  "duplicated": false
}
```

## 대량 발급

```http
POST /coupons/issue/bulk
```

```json
{
  "couponCode": "AUTUMN-2025",
  "referenceKeyPrefix": "AUTUMN-2025",
  "memberIds": [1024, 1025, 1026]
}
```

`referenceKey` 는 `{prefix}:{memberId}` 로 자동 생성됩니다. 한 번에 최대 1,000명까지 요청할 수 있습니다.

응답:

```json
{
  "requested": 3,
  "issued": 2,
  "duplicated": 1,
  "failed": 0,
  "failures": []
}
```

부분 성공을 허용합니다. 실패 건은 `failures` 에 회원 ID 와 사유가 들어갑니다.

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| COUPON_1001 | 404 | 존재하지 않는 쿠폰 코드 |
| COUPON_1002 | 409 | 발급 기간이 아님 |
| COUPON_1003 | 409 | 발급 대상 아님 (회원 상태) |
| COUPON_1004 | 400 | 요청 인원 초과 (1,000 초과) |

## 주의사항

대량 발급은 부분 성공이 정상 동작입니다. HTTP 200 이어도 `failed` 를 확인해야 합니다. 재실행 시 이미 발급된 건은 `duplicated` 로 집계되며 중복 발급되지 않습니다.

발급 대상이 1,000명을 넘으면 호출자가 나눠서 보내야 합니다. 이벤트 배치에서는 이 분할이 이미 구현되어 있습니다.
""",
    },
    {
        "id": "DOC-071",
        "title": "동일 쿠폰이 두 주문에 적용되는 문제",
        "department": "QA팀",
        "type": "이슈 정리",
        "project": "Project Nova",
        "date": "2025-10-16",
        "systems": ["MARS", "ORBIT"],
        "features": ["쿠폰 사용", "주문"],
        "participants": ["윤수빈", "박준호"],
        "concepts": ["동시성", "중복 사용", "Race Condition"],
        "body": """## 현상

윤수빈님이 QA 중 같은 쿠폰을 두 개의 주문에 사용하는 데 성공했습니다. 두 주문 모두 할인이 적용되었고 쿠폰 상태는 `USED` 한 번만 기록되었습니다.

## 재현 조건

1. 쿠폰 하나를 보유한 계정으로 브라우저 두 개에서 각각 주문서 진입
2. 양쪽에서 같은 쿠폰 선택
3. 거의 동시에 결제 진행

수동으로도 2~3회 시도하면 재현됩니다.

## 분석

쿠폰 사용 처리가 이렇게 되어 있습니다.

```java
var issue = couponIssueRepository.find(issueId);
if (issue.status() != ISSUED) {
    throw new CouponAlreadyUsedException();
}
issue.use(orderId);
couponIssueRepository.save(issue);
```

조회 시점에 둘 다 `ISSUED` 를 보고 통과합니다. 저장은 나중에 커밋된 쪽이 앞의 값을 덮어씁니다. 적립금에서 겪은 문제와 같은 형태인데 쿠폰은 이번 개선 범위에 없어서 그대로 남아 있었습니다.

## 영향 범위

- 스테이징에서만 확인. 운영 데이터에서 유사 사례를 조회한 결과 지난 6개월간 4건 발견
- 4건 모두 금액이 크지 않아 별도 회수는 하지 않기로 함

## 임시 대응

없습니다. 재현 난도가 있어 즉시 조치 없이 정식 수정으로 갑니다.

## 근본 해결

박준호님이 상태 조건을 갱신 쿼리에 넣는 방식으로 수정합니다.

```sql
UPDATE coupon_issue
SET status = 'USED', used_at = NOW(), order_id = :orderId
WHERE id = :issueId AND status = 'ISSUED';
```

영향 행이 0이면 이미 사용된 것으로 처리합니다.

## 재발 방지

조회 후 판단하고 저장하는 패턴이 다른 곳에도 있을 수 있습니다. 상태 전이가 있는 도메인을 한 번 훑어보기로 했습니다. 아직 착수하지 않았습니다.
""",
    },
    {
        "id": "DOC-072",
        "title": "부분 환불 시 리워드 회수 기준 논의",
        "department": "서비스기획팀",
        "type": "기획자와의 회의록",
        "project": "Project Nova",
        "date": "2025-10-22",
        "systems": ["MARS", "ORBIT"],
        "features": ["부분 취소", "리워드 회수", "환불"],
        "participants": ["송지은", "강민재"],
        "concepts": ["부분 취소", "정책 미확정", "배분"],
        "body": """## 논의 배경

부분 취소에서 적립금을 어떻게 처리할지 정하지 못해 현재 적립금을 쓴 주문은 부분 취소가 막혀 있습니다. 고객지원팀 문의가 늘고 있어 결론이 필요합니다.

## 정리해야 할 것

1. 주문에 사용한 적립금 중 얼마를 돌려줄지
2. 주문으로 적립된 적립금 중 얼마를 회수할지

## 검토한 배분 방식

3개 상품(10,000 / 20,000 / 30,000)에 적립금 6,000점을 쓰고 20,000원짜리를 취소하는 경우입니다.

| 방식 | 반환 적립금 | 설명 |
| --- | --- | --- |
| 금액 비율 배분 | 2,000 | 상품 금액 비율로 나눔 |
| 취소 상품 우선 | 6,000 | 취소분에 적립금을 먼저 배정 |
| 현금 우선 | 0 | 현금부터 환불, 적립금은 마지막 |

강민재님은 금액 비율 배분이 가장 설명하기 쉽다고 봤습니다. 송지은님은 사용자 입장에서 "적립금을 먼저 돌려받는" 쪽이 체감이 좋다고 했습니다.

## 쟁점

취소 상품 우선 방식은 순차 취소에서 문제가 생깁니다. 첫 취소에서 적립금을 전부 돌려주면 두 번째 취소에서 돌려줄 적립금이 없는데, 사용자는 여전히 적립금을 썼다고 인식합니다.

현금 우선은 회계 처리가 단순하지만 마지막 상품을 취소할 때까지 적립금이 안 돌아옵니다.

## 결정 사항

- 배분 방식은 **금액 비율**로 합니다
- 단수는 사용자에게 유리하게 올림 처리합니다

## 미확정

적립 회수 기준은 정하지 못했습니다. 적립은 최종 결제 금액 기준으로 계산되는데, 부분 취소 후 남은 금액으로 다시 계산해서 차액을 회수할지, 취소 상품의 적립분만 회수할지 의견이 갈렸습니다.

송지은님이 두 방식의 실제 차이를 사례로 정리한 뒤 다음 주 2차 회의를 진행하기로 했습니다. 그때까지 부분 취소 제한은 유지합니다.
""",
    },
    {
        "id": "DOC-073",
        "title": "Nova 마이그레이션 리허설 결과",
        "department": "프로젝트관리팀",
        "type": "프로젝트 진행상황",
        "project": "Project Nova",
        "date": "2025-10-29",
        "systems": ["MARS"],
        "features": ["포인트"],
        "participants": ["임채원", "오세훈"],
        "concepts": ["마이그레이션", "점검", "롤백"],
        "body": """## 리허설 개요

10월 28일 새벽, 운영 데이터 복제본으로 마이그레이션을 리허설했습니다. 목표는 점검 시간 30분 안에 완료하는 것입니다.

## 대상 규모

| 항목 | 건수 |
| --- | --- |
| 구 적립금 잔액 | 2,140,332 |
| 구 이벤트 포인트 | 872,104 |
| 생성될 reward_history | 3,012,436 |
| 생성될 reward_balance | 1,884,201 |

## 결과

| 단계 | 소요 |
| --- | --- |
| 구 데이터 추출 | 4분 12초 |
| reward_history 적재 | 18분 40초 |
| reward_balance 집계 | 6분 05초 |
| 정합성 대조 검증 | 3분 30초 |
| 합계 | 32분 27초 |

목표를 2분 넘겼습니다.

## 개선안

오세훈님 확인 결과 `reward_history` 적재에서 인덱스가 이미 걸린 상태로 INSERT 하고 있었습니다. 인덱스를 나중에 만들면 적재 시간이 줄어듭니다. 다만 인덱스 생성 자체에 시간이 들어 총합이 얼마나 줄지는 재측정이 필요합니다.

## 검증 결과

정합성 대조에서 총 잔액이 일치했습니다. 다만 개별 회원 12명에서 차이가 났고, 확인해 보니 구 데이터에서 이미 잔액과 이력이 어긋나 있던 계정이었습니다. 마이그레이션이 만든 문제가 아닙니다.

이 12명은 이관 시 이력 기준으로 잔액을 맞춥니다. 기존 잔액이 이력보다 큰 경우가 9건이라 사용자 잔액이 줄어듭니다. 금액이 크지 않지만 고지 없이 줄이면 문의가 생깁니다. 기획팀 확인이 필요합니다.

## 롤백 확인

구 테이블을 그대로 두므로 롤백은 애플리케이션 설정을 되돌리는 것으로 가능합니다. 리허설에서 롤백 절차도 함께 확인했고 5분 안에 완료됩니다.

다만 마이그레이션 이후 발생한 신규 거래는 구 테이블에 없습니다. 롤백 시점 이후 거래는 수동 반영이 필요합니다. 점검 직후 짧은 시간 안에만 롤백이 유효합니다.

## 다음 일정

- 11월 4일 2차 리허설 (인덱스 순서 개선 적용)
- 11월 8일 운영 마이그레이션 목표
""",
    },
    {
        "id": "DOC-074",
        "title": "적립금 관련 장애 대응 가이드",
        "department": "백엔드개발팀",
        "type": "매뉴얼 / 정의서",
        "project": None,
        "date": "2025-11-04",
        "systems": ["MARS"],
        "features": ["포인트", "리워드 지급"],
        "participants": [],
        "concepts": ["장애 대응", "운영", "대사"],
        "body": """## 증상별 1차 확인

| 증상 | 먼저 볼 것 |
| --- | --- |
| 잔액이 화면마다 다르게 보임 | 캐시 여부, 정합성 대조 배치 결과 |
| 사용이 계속 실패 | `REWARD_2009` 비율, DB 커넥션 |
| 지급이 안 됨 | 참조키 중복 여부, 호출자 로그 |
| 잔액이 갑자기 줄어듦 | 소멸 배치 실행 이력 |

## 정합성 대조 불일치 발생 시

1. 알림에 포함된 회원 목록 확인
2. 해당 회원의 `reward_history` 를 시간순으로 조회
3. 이력 합계와 `reward_balance` 차이 계산
4. 원인 파악 전까지 **잔액을 고치지 않습니다**

원인을 모른 채 잔액만 맞추면 같은 문제가 반복되고 흔적이 사라집니다. 사용자 문의가 들어와 급한 경우에도 보정 전에 이력 스냅샷을 남기세요.

## 지급 중복 의심 시

```sql
SELECT reason, reference_key, COUNT(*)
FROM reward_history
WHERE type = 'GRANT' AND created_at >= :since
GROUP BY reason, reference_key
HAVING COUNT(*) > 1;
```

unique 제약이 있어 정상적으로는 나오지 않습니다. 결과가 있으면 제약이 빠진 경로가 있다는 뜻이므로 즉시 공유하세요.

## 소멸 배치 이상

소멸 배치는 실행 이력을 남깁니다. 특정 날짜에 소멸 금액이 평소의 몇 배라면 만료일 계산이 잘못됐을 수 있습니다. 배치를 중단하고 대상 목록을 먼저 확인하세요.

한 번 소멸시킨 적립금은 자동으로 되돌릴 수 없습니다. 잘못 소멸시킨 경우 보상 지급으로 처리하며 사유 코드는 `COMPENSATION` 을 씁니다.

## 에스컬레이션

- 영향 회원 100명 이상: 백엔드 리드 즉시 공유
- 금액 100만 적립금 이상: 기획팀 동시 공유
- 정합성 대조 불일치가 이틀 연속 발생: 배치 중단 후 원인 분석 우선
""",
    },
    {
        "id": "DOC-075",
        "title": "Nova 배포 후 회고",
        "department": "프로젝트관리팀",
        "type": "프로젝트 진행상황",
        "project": "Project Nova",
        "date": "2025-11-12",
        "systems": ["MARS"],
        "features": ["포인트", "리워드 지급", "쿠폰 지급"],
        "participants": ["임채원", "박준호", "김도윤"],
        "concepts": ["회고", "설계 변경", "일정"],
        "body": """## 배포 결과

11월 8일 새벽 마이그레이션과 배포를 완료했습니다. 점검 시간은 27분으로 목표 안에 들어왔습니다. 배포 후 4일간 정합성 대조 불일치는 없었습니다.

## 잘된 점

- 정합성 대조 배치를 처음부터 만들어 둔 것이 유효했습니다. 9월 불일치 12건을 이틀 만에 잡았고, 마이그레이션 검증에도 그대로 썼습니다.
- 리허설을 두 번 한 덕분에 점검 시간을 예측할 수 있었습니다.
- 참조키 기반 멱등 처리로 이벤트 배치 재실행이 안전해졌습니다.

## 아쉬운 점

박준호님: 초기 설계에서 동시성을 충분히 검토하지 않았습니다. 잔액을 단일 컬럼으로 두는 안이 조회 성능만 보고 결정됐고, 사용 시나리오의 경합을 나중에야 봤습니다. 설계 리뷰 단계에서 부하 시나리오를 같이 봤어야 합니다.

김도윤님: 문제 자체는 이르게 발견됐습니다. 8월 부하 테스트가 없었으면 운영에서 터졌을 겁니다. 다만 그 시점에 이미 API 명세와 코드리뷰 문서가 옛 구조 기준으로 나가 있어서, 폐기된 문서가 검색에 계속 걸립니다. 문서에 폐기 표시를 하는 규칙이 필요합니다.

임채원님: 일정이 한 스프린트 밀렸지만 재작업 판단은 옳았습니다. 다만 밀린다는 사실을 공유하는 데 며칠 걸렸습니다.

## 남은 과제

- 부분 취소 시 리워드 처리 정책 미확정. 현재 적립금 사용 주문은 부분 취소 불가
- 쿠폰 사용 조건 데이터화, 선착순 발급
- 정합성 대조 배치 주기 단축 (일 1회 → 6시간)
- 폐기 문서 표시 규칙

## 다음

부분 취소 정책이 확정되면 Falcon 결제 개편과 함께 처리하는 편이 낫다는 의견이 있었습니다. 결제 취소 흐름 자체를 손보는 프로젝트가 곧 시작하므로 그쪽에서 같이 다룹니다.
""",
    },
]
