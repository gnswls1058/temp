"""DOC-196 ~ DOC-210 : 리뷰, 문의, 상품 카테고리와 검색 (2025~2026)."""

DOCUMENTS = [
    {
        "id": "DOC-196",
        "title": "order_summary 집계 테이블 설계",
        "department": "데이터팀",
        "type": "DB 테이블 설계안",
        "project": None,
        "date": "2026-02-13",
        "systems": ["ORBIT"],
        "features": ["주문"],
        "participants": [],
        "concepts": ["집계", "파이프라인", "정합성"],
        "body": """## 배경

등급 산정, 통계, 정산이 모두 주문 원본 테이블을 조회합니다. 조회 조건이 제각각이고 결과가 서로 다른 경우가 있습니다.

## 목적

주문 한 건을 한 행으로 요약한 집계 테이블을 만들어 분석 계층의 기준으로 삼습니다.

## 테이블

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| order_id | BIGINT | PK |
| member_id | BIGINT | |
| ordered_at | DATETIME | |
| status | VARCHAR(24) | 최종 상태 |
| item_count | INT | 상품 종류 수 |
| total_quantity | INT | 총 수량 |
| gross_amount | INT | 할인 전 금액 |
| discount_amount | INT | 쿠폰 할인 |
| point_amount | INT | 포인트 사용 |
| paid_amount | INT | 실 결제 금액 |
| canceled_amount | INT | 누적 취소 금액 |
| net_paid_amount | INT | paid - canceled |
| applied_reward_rate | DECIMAL(4,3) | 주문 시 적립률 |
| delivered_at | DATETIME | |
| updated_at | DATETIME | 증분 적재 기준 |

## 왜 net_paid_amount 를 따로 두는가

등급 산정, 정산, 통계가 모두 "실제로 얼마를 냈는가"를 봅니다. 매번 `paid - canceled` 를 계산하면 어딘가에서 취소를 빼먹습니다.

실제로 등급 산정에서 취소 반영이 누락된 적이 있었습니다. 계산된 값을 저장해 기준을 하나로 만듭니다.

## 적재 방식

`updated_at` 기준 증분 적재입니다. 주문이나 결제, 취소가 발생하면 원본의 `updated_at` 이 갱신되고 다음 적재에서 반영됩니다.

```sql
SELECT ... FROM orders o
WHERE o.updated_at >= :lastSyncedAt
```

## 지연

하루 1회 적재이므로 최대 하루 지연됩니다. 등급 산정은 이를 고려해 매월 2일에 수행합니다.

실시간 지표가 필요한 화면은 이 테이블을 쓰지 않고 원본을 조회합니다.

## 정합성 검증

일 1회 원본과 정합성 대조합니다.

- 전일 주문 건수
- 전일 결제 금액 합계
- 취소 금액 합계

차이가 있으면 알림을 보냅니다. 자동 보정하지 않고 원인을 확인합니다.

## 인덱스

```sql
CREATE INDEX idx_summary_member_date ON order_summary (member_id, ordered_at);
CREATE INDEX idx_summary_updated ON order_summary (updated_at);
CREATE INDEX idx_summary_status_date ON order_summary (status, ordered_at);
```

첫 번째는 등급 산정용입니다. 회원별 기간 집계가 이 인덱스로 처리됩니다.
""",
    },
    {
        "id": "DOC-197",
        "title": "review / review_image 테이블 설계",
        "department": "백엔드개발팀",
        "type": "DB 테이블 설계안",
        "project": None,
        "date": "2026-03-31",
        "systems": ["ORBIT", "MARS"],
        "features": ["상품"],
        "participants": [],
        "concepts": ["리워드 지급", "Unique Constraint", "이미지"],
        "body": """## 배경

리뷰 작성 시 포인트를 지급하기로 하면서 테이블을 정리합니다. 어뷰징 방지를 위한 제약이 핵심입니다.

## review

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| id | BIGINT | PK | |
| order_item_id | BIGINT | UNIQUE | 주문 상품당 1개 |
| member_id | BIGINT | NOT NULL | |
| product_id | BIGINT | NOT NULL | |
| rating | TINYINT | NOT NULL | 1~5 |
| content | TEXT | NOT NULL | |
| status | VARCHAR(16) | NOT NULL | PUBLISHED / HIDDEN / DELETED |
| reward_granted | BOOLEAN | NOT NULL DEFAULT false | |
| reward_amount | INT | NOT NULL DEFAULT 0 | |
| created_at | DATETIME | NOT NULL | |
| updated_at | DATETIME | NOT NULL | |

```sql
CREATE UNIQUE INDEX uk_review_order_item ON review (order_item_id);
CREATE INDEX idx_review_product ON review (product_id, status, created_at DESC);
CREATE INDEX idx_review_member ON review (member_id, created_at DESC);
```

## order_item_id unique

주문 상품 하나에 리뷰 하나만 허용합니다. 이 제약이 중복 포인트 지급을 막는 1차 방어선입니다.

포인트 지급 참조키도 `REVIEW:{orderItemId}` 형태라 MARS 쪽에서도 중복이 막힙니다. 이중으로 막습니다.

## review_image

| 컬럼 | 설명 |
| --- | --- |
| id | PK |
| review_id | FK |
| image_url | 저장소 URL |
| display_order | 노출 순서 |
| created_at | |

이미지는 최대 5장입니다. 리뷰 유형(텍스트/사진)은 이미지 존재 여부로 판단합니다. 별도 컬럼을 두지 않습니다.

## 삭제 처리

물리 삭제하지 않고 `status = DELETED` 로 둡니다. 삭제해도 `order_item_id` unique 가 유지되어야 재작성으로 포인트를 다시 받을 수 없습니다.

이미지 파일은 삭제 30일 뒤 저장소에서 지웁니다. 신고 처리 중에 원본이 필요할 수 있습니다.

## reward_granted 를 두는 이유

포인트 지급 여부를 리뷰 쪽에서도 알아야 합니다. 삭제 시 회수 대상인지 판단하는 데 씁니다. MARS 에 매번 물어보지 않기 위한 값입니다.

`reward_amount` 는 지급 당시 금액입니다. 정책이 바뀌어도 회수 금액은 지급액과 같아야 합니다.

## 상태 전이

```text
PUBLISHED -> HIDDEN     (신고 접수, 운영자 처리)
PUBLISHED -> DELETED    (작성자 삭제)
HIDDEN    -> PUBLISHED  (신고 반려)
HIDDEN    -> DELETED    (운영자 삭제)
DELETED   -> (없음)
```

`HIDDEN` 상태에서는 포인트를 회수하지 않습니다. 신고가 반려될 수 있기 때문입니다.
""",
    },
    {
        "id": "DOC-198",
        "title": "리뷰 작성 API 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": None,
        "date": "2026-04-03",
        "systems": ["ORBIT", "MARS"],
        "features": ["상품", "리워드 지급"],
        "participants": [],
        "concepts": ["Validation", "리워드 지급", "Error Code"],
        "body": """## Endpoint

```http
POST /reviews
Authorization: Bearer {accessToken}
```

## Request

```json
{
  "orderItemId": 551201,
  "rating": 5,
  "content": "배송도 빠르고 음질이 좋습니다.",
  "imageUrls": [
    "https://cdn.example.com/review/tmp/a1b2c3.jpg"
  ]
}
```

이미지는 먼저 업로드 API 로 올리고 반환된 임시 URL 을 전달합니다. 리뷰 저장 시 정식 경로로 이동합니다.

## 작성 조건

| 조건 | 설명 |
| --- | --- |
| 배송 완료 | `DELIVERED` 상태인 주문 상품만 |
| 작성 기한 | 배송 완료 후 30일 |
| 중복 | 주문 상품당 1회 |
| 본문 길이 | 10자 이상 2,000자 이하 |
| 이미지 | 최대 5장 |

## Response

```json
{
  "reviewId": 88120,
  "status": "PUBLISHED",
  "rewardGranted": true,
  "rewardAmount": 500,
  "createdAt": "2026-04-03T14:22:00+09:00"
}
```

`rewardGranted` 가 false 여도 리뷰는 정상 등록된 것입니다. 월 지급 한도(20회)를 넘었거나 지급에 실패한 경우입니다.

## 포인트 지급

| 유형 | 포인트 |
| --- | --- |
| 텍스트만 | 100 |
| 이미지 포함 | 500 |

월 20회까지 지급됩니다. 초과분은 리뷰만 등록되고 포인트는 없습니다.

지급이 실패해도 리뷰 등록은 롤백하지 않습니다. 지급은 별도 트랜잭션이며 실패 시 재시도 큐로 넘어갑니다.

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| REVIEW_1001 | 409 | 이미 리뷰가 존재 |
| REVIEW_1002 | 409 | 배송 완료 상태가 아님 |
| REVIEW_1003 | 409 | 작성 기한 초과 |
| REVIEW_1004 | 403 | 본인 주문이 아님 |
| REVIEW_1005 | 400 | 본문 길이 위반 |
| REVIEW_1006 | 400 | 이미지 개수 초과 |
| REVIEW_1007 | 400 | 유효하지 않은 이미지 URL |

## 삭제

```http
DELETE /reviews/{reviewId}
```

삭제 시 지급된 포인트를 회수합니다. 이미 사용했으면 회수 불가로 집계되며 잔액을 음수로 만들지 않습니다.
""",
    },
    {
        "id": "DOC-199",
        "title": "리뷰 사진 첨부가 간헐적으로 실패",
        "department": "백엔드개발팀",
        "type": "이슈 정리",
        "project": None,
        "date": "2026-04-10",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": [],
        "concepts": ["이미지", "외부 저장소", "타임아웃"],
        "body": """## 현상

리뷰 작성 시 이미지 업로드가 실패하는 경우가 있습니다. 4월 8~9일 기준 업로드 시도 대비 실패율 4.2% 입니다.

## 재현

일관되게 재현되지 않습니다. 같은 이미지를 다시 올리면 성공하는 경우가 많습니다.

## 로그

```text
POST /reviews/images  status=500 duration=10021ms
  java.net.SocketTimeoutException: Read timed out
  at storageClient.upload(StorageClient.java:88)
```

10초 타임아웃에 걸립니다.

## 분석

실패 건의 이미지 크기를 확인했습니다.

| 크기 | 시도 | 실패율 |
| --- | --- | --- |
| ~1MB | 12,400 | 0.3% |
| 1~3MB | 5,210 | 2.1% |
| 3~5MB | 1,880 | 14.7% |
| 5MB~ | 620 | 38.2% |

큰 이미지에서 실패가 집중됩니다. 최신 스마트폰 사진이 5MB 를 넘는 경우가 흔합니다.

업로드는 서버가 받아서 저장소로 전달하는 구조입니다. 클라이언트 → 서버 → 저장소 두 단계를 거치므로 시간이 두 배로 듭니다.

## 조치

**즉시**: 타임아웃을 10초에서 30초로 늘렸습니다. 실패율이 4.2% 에서 0.9% 로 떨어졌습니다.

**클라이언트**: 업로드 전 이미지를 리사이즈합니다. 긴 변 1,600px, 품질 85% 로 압축하면 대부분 1MB 이하가 됩니다. 리뷰 이미지는 그 이상 해상도가 필요 없습니다.

**구조 개선**: 서버를 거치지 않고 클라이언트가 저장소에 직접 업로드하는 방식(사전 서명 URL)을 검토합니다. 서버 부하와 지연이 모두 줄어듭니다.

## 남은 작업

클라이언트 리사이즈는 앱 릴리스에 포함되어 4월 중순 반영됩니다. 웹은 이번 주 배포합니다.

사전 서명 URL 방식은 별도 과제로 등록했습니다. 저장소 권한 설계가 필요해 범위가 작지 않습니다.

## 배운 점

타임아웃 값이 근거 없이 정해져 있었습니다. 10초는 어디서 온 값인지 아무도 모릅니다. 외부 호출 타임아웃은 실제 응답 시간 분포를 보고 정해야 합니다.
""",
    },
    {
        "id": "DOC-200",
        "title": "[리뷰] 리뷰 이미지 업로드 처리",
        "department": "백엔드개발팀",
        "type": "코드리뷰",
        "project": None,
        "date": "2026-04-15",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": ["정하늘", "오세훈"],
        "concepts": ["이미지", "임시 파일", "정리 배치"],
        "body": """## 현재 구현

업로드된 이미지를 임시 경로에 두고, 리뷰 저장 시 정식 경로로 옮깁니다.

```java
public String upload(MultipartFile file) {
    validate(file);
    var key = "review/tmp/" + UUID.randomUUID() + extension(file);
    storageClient.put(key, file.getInputStream());
    return cdnBaseUrl + key;
}
```

## 리뷰 의견

오세훈님: 임시 파일이 정리되지 않습니다. 업로드만 하고 리뷰를 안 쓰면 영원히 남습니다. 저장소 비용이 계속 늘어납니다.

정하늘님: 정리 배치를 추가하겠습니다. 임시 경로에서 24시간 이상 된 파일을 지웁니다.

오세훈님: 24시간은 넉넉한가요. 사용자가 이미지를 올리고 리뷰를 나중에 쓰는 경우가 있을 수 있습니다.

정하늘님: 화면 흐름상 이미지 업로드와 리뷰 저장이 같은 세션에서 일어납니다. 24시간이면 충분합니다.

오세훈님: 파일 검증이 확장자만 보고 있습니다. 확장자를 바꾼 실행 파일을 올릴 수 있습니다. 실제 내용을 확인해야 합니다.

정하늘님: 매직 넘버로 이미지 형식을 확인하겠습니다.

오세훈님: 저장 시 원본 파일명을 쓰지 않는 건 좋습니다. 다만 반환하는 URL 이 CDN 경로 그대로라 임시 파일에 누구나 접근할 수 있습니다. 다른 사람이 올린 임시 이미지를 자기 리뷰에 붙일 수 있나요.

정하늘님: 지금은 가능합니다. 리뷰 저장 시 URL 이 임시 경로인지만 확인하고 누가 올렸는지는 안 봅니다.

오세훈님: 업로더를 기록하고 리뷰 저장 시 대조해야 합니다.

## 수정 사항

- 임시 파일 정리 배치 (24시간)
- 매직 넘버 기반 형식 검증
- 업로더 기록 및 리뷰 저장 시 대조
- 파일 크기 상한 10MB

## 남은 확인

사전 서명 URL 방식으로 바꾸면 이 구조가 통째로 달라집니다. 그전까지 임시 대응으로 두고, 전환 시 업로더 대조 방식도 함께 재설계합니다.
""",
    },
    {
        "id": "DOC-201",
        "title": "리뷰 노출 정렬 기준 논의",
        "department": "서비스기획팀",
        "type": "기획자와의 회의록",
        "project": None,
        "date": "2026-04-24",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": ["송지은", "최유진"],
        "concepts": ["정렬", "UX", "어뷰징"],
        "body": """## 논의 배경

리뷰가 최신순으로만 정렬됩니다. 포인트 지급을 시작한 뒤 짧은 리뷰가 늘어 상단이 "좋아요", "잘 쓸게요" 같은 내용으로 채워집니다.

## 현황

최유진님이 확인한 최근 2주 리뷰 분포입니다.

| 본문 길이 | 비중 |
| --- | --- |
| 10~20자 | 46% |
| 21~50자 | 31% |
| 51자 이상 | 23% |

포인트 도입 전에는 10~20자가 12% 였습니다.

## 검토안

| 정렬 | 장점 | 단점 |
| --- | --- | --- |
| 최신순 (현행) | 단순, 공정 | 품질 낮은 리뷰 상단 |
| 도움돼요 순 | 유용한 리뷰 상단 | 초기 리뷰가 유리 |
| 사진 우선 | 정보량 많음 | 사진만 있고 내용 없는 리뷰 |
| 복합 점수 | 균형 | 기준 설명이 어려움 |

## 쟁점

송지은님은 사용자가 정렬을 고를 수 있게 하자고 했습니다. 기본은 "추천순"이고 최신순도 선택할 수 있습니다.

최유진님은 추천순의 계산 기준이 불투명하면 판매자 항의가 들어올 수 있다고 우려했습니다. 자기 상품의 좋은 리뷰가 아래로 밀리면 문제 제기가 나옵니다.

## 결정 사항

- 기본 정렬을 **복합 점수**로 하고 최신순 선택지를 제공합니다
- 복합 점수 = 도움돼요 수 + 이미지 여부 + 본문 길이 구간 + 최신성
- 가중치는 설정으로 관리하고 초기값을 조정 가능하게 합니다

## 가중치 초기값

| 요소 | 가중치 |
| --- | --- |
| 도움돼요 1건 | 3점 |
| 이미지 포함 | 10점 |
| 본문 50자 이상 | 5점 |
| 최근 7일 | 5점 |

근거가 강한 값은 아닙니다. 배포 후 상단 노출 리뷰의 특성을 보고 조정합니다.

## 미확정

낮은 평점 리뷰를 어떻게 다룰지 정하지 못했습니다. 복합 점수만 쓰면 평점과 무관하게 정렬되는데, 판매자 입장에서는 부정적 리뷰가 상단에 오래 남는 것을 우려합니다.

평점을 정렬에 넣으면 조작으로 보일 수 있어 넣지 않는 쪽으로 기울었지만 결론은 다음에 냅니다.
""",
    },
    {
        "id": "DOC-202",
        "title": "inquiry / inquiry_answer 테이블 설계",
        "department": "백엔드개발팀",
        "type": "DB 테이블 설계안",
        "project": None,
        "date": "2025-06-24",
        "systems": ["ORBIT", "AIMS"],
        "features": [],
        "participants": [],
        "concepts": ["문의", "상태값 관리", "Index"],
        "body": """## 배경

문의가 게시판 형태로 저장되어 있어 처리 상태를 관리할 수 없습니다. 답변 여부만 알 수 있고, 담당자나 처리 이력이 없습니다.

## inquiry

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| id | BIGINT | PK | |
| member_id | BIGINT | NULL | 비회원 문의 허용 |
| category | VARCHAR(32) | NOT NULL | ORDER / DELIVERY / PAYMENT / REFUND / PRODUCT / ACCOUNT / ETC |
| order_id | BIGINT | NULL | 관련 주문 |
| title | VARCHAR(200) | NOT NULL | |
| content | TEXT | NOT NULL | |
| status | VARCHAR(16) | NOT NULL | RECEIVED / IN_PROGRESS / ANSWERED / CLOSED |
| assignee_id | BIGINT | NULL | 담당 운영자 |
| is_secret | BOOLEAN | NOT NULL | 비공개 여부 |
| created_at | DATETIME | NOT NULL | |
| answered_at | DATETIME | NULL | |

## inquiry_answer

| 컬럼 | 설명 |
| --- | --- |
| id | PK |
| inquiry_id | FK |
| answerer_id | 답변한 운영자 |
| content | 답변 본문 |
| created_at | |

답변이 여러 개일 수 있습니다. 추가 질문에 다시 답하는 경우입니다.

```sql
CREATE INDEX idx_inquiry_member ON inquiry (member_id, created_at DESC);
CREATE INDEX idx_inquiry_status ON inquiry (status, created_at);
CREATE INDEX idx_inquiry_assignee ON inquiry (assignee_id, status);
CREATE INDEX idx_inquiry_order ON inquiry (order_id);
```

## 비회원 문의

`member_id` 가 null 인 경우입니다. 이때 연락처를 받아야 답변할 수 있는데, 개인정보라 별도 테이블에 암호화해 저장합니다.

`inquiry_contact` 테이블에 이메일 또는 전화번호를 두고, 문의 종료 후 90일에 삭제합니다.

## 상태 전이

```text
RECEIVED    -> IN_PROGRESS  (담당자 배정)
IN_PROGRESS -> ANSWERED     (답변 등록)
ANSWERED    -> IN_PROGRESS  (추가 질문)
ANSWERED    -> CLOSED       (7일 경과 자동 종료)
```

`CLOSED` 이후에는 추가 질문을 받지 않고 새 문의로 안내합니다.

## 담당자 배정

자동 배정은 하지 않습니다. 운영자가 목록에서 선택해 가져갑니다. 자동 배정은 담당자 부재나 업무량 편차를 반영하지 못합니다.

`idx_inquiry_assignee` 는 "내 문의함" 조회용입니다.

## 고민

문의 본문에 주문번호나 개인정보가 들어옵니다. 보관 기간을 정해야 하는데, 답변 이력은 오래 참고하는 경우가 있습니다.

3년 보관 후 삭제하는 것으로 정했습니다. 비회원 연락처만 90일로 짧게 둡니다.
""",
    },
    {
        "id": "DOC-203",
        "title": "문의 등록 API 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": None,
        "date": "2025-07-02",
        "systems": ["ORBIT"],
        "features": [],
        "participants": [],
        "concepts": ["문의", "비회원", "Validation"],
        "body": """## Endpoint

```http
POST /inquiries
```

회원은 `Authorization` 헤더를 포함합니다. 비회원도 등록할 수 있습니다.

## Request (회원)

```json
{
  "category": "DELIVERY",
  "orderId": 90112,
  "title": "배송이 지연되고 있습니다",
  "content": "3일째 배송 중 상태입니다.",
  "isSecret": true
}
```

## Request (비회원)

```json
{
  "category": "PRODUCT",
  "title": "상품 문의",
  "content": "재입고 예정이 있나요?",
  "isSecret": false,
  "contact": {
    "email": "guest@example.com"
  },
  "password": "조회용 비밀번호"
}
```

비회원은 조회용 비밀번호를 설정합니다. 이 값은 해시로 저장되며 문의 조회 시 필요합니다.

## Response

```json
{
  "inquiryId": 77120,
  "status": "RECEIVED",
  "expectedAnswerAt": "2025-07-03T18:00:00+09:00",
  "createdAt": "2025-07-02T14:22:00+09:00"
}
```

`expectedAnswerAt` 은 영업일 기준 1일 뒤입니다. 실제 답변 시각을 보장하지 않습니다.

## 제한

| 항목 | 값 |
| --- | --- |
| 제목 | 5~200자 |
| 본문 | 10~5,000자 |
| 회원 등록 한도 | 일 10건 |
| 비회원 등록 한도 | IP 기준 일 5건 |

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| INQUIRY_1001 | 400 | 필수 값 누락 |
| INQUIRY_1002 | 400 | 알 수 없는 카테고리 |
| INQUIRY_1003 | 429 | 등록 한도 초과 |
| INQUIRY_1004 | 403 | 본인 주문이 아님 (orderId 지정 시) |
| INQUIRY_1005 | 400 | 비회원인데 연락처 누락 |

## 주의사항

`orderId` 를 지정하면 해당 주문의 소유자인지 검증합니다. 다른 사람 주문번호를 넣으면 403 입니다.

본문에 개인정보를 적지 않도록 화면에 안내를 넣어 주세요. 카드번호나 주민번호가 문의 본문에 들어오는 사례가 있습니다.
""",
    },
    {
        "id": "DOC-204",
        "title": "문의 카테고리 재정비",
        "department": "고객지원팀",
        "type": "개발자 회의록",
        "project": None,
        "date": "2025-08-06",
        "systems": ["AIMS"],
        "features": [],
        "participants": ["문가영", "강민재"],
        "concepts": ["문의", "분류", "운영 효율"],
        "body": """## 논의 배경

문의 카테고리가 7개인데 절반 이상이 `ETC` 로 들어옵니다. 사용자가 어디에 넣을지 몰라 기타를 선택합니다.

## 현황

문가영님이 정리한 지난달 분포입니다.

| 카테고리 | 비중 |
| --- | --- |
| ETC | 54% |
| DELIVERY | 18% |
| ORDER | 11% |
| REFUND | 8% |
| PAYMENT | 5% |
| PRODUCT | 3% |
| ACCOUNT | 1% |

`ETC` 문의를 실제로 열어보면 배송이나 환불 문의가 많습니다.

## 원인

카테고리 이름이 추상적입니다. "주문"과 "결제"의 차이를 사용자가 구분하기 어렵습니다.

또 카테고리가 목록 맨 위에 있어서 문의 내용을 쓰기 전에 골라야 합니다. 무엇을 쓸지 정하기 전에 분류부터 요구하는 셈입니다.

## 검토안

**1. 카테고리를 질문 형태로 바꾸기**

"배송이 안 와요", "환불받고 싶어요" 처럼 사용자 언어로 바꿉니다.

**2. 카테고리를 없애고 자동 분류**

본문을 분석해 자동으로 분류합니다. 정확도가 문제이고 구현 범위가 큽니다.

**3. 카테고리 순서 변경**

내용을 먼저 쓰고 마지막에 분류를 고릅니다.

## 결정 사항

1안과 3안을 함께 적용합니다.

새 카테고리:

| 코드 | 표시 문구 |
| --- | --- |
| DELIVERY | 배송이 늦거나 안 와요 |
| ORDER_CHANGE | 주문을 바꾸거나 취소하고 싶어요 |
| REFUND | 환불을 받고 싶어요 |
| PAYMENT | 결제가 안 되거나 잘못됐어요 |
| PRODUCT | 상품에 대해 궁금해요 |
| ACCOUNT | 로그인이나 계정 문제예요 |
| REWARD | 적립금나 쿠폰 문제예요 |
| ETC | 그 외 문의 |

`REWARD` 를 신설했습니다. 적립금 문의가 `ETC` 에 많았습니다.

## 마이그레이션

기존 문의의 카테고리는 그대로 둡니다. 코드가 대부분 유지되고 `ORDER` 만 `ORDER_CHANGE` 로 바뀝니다.

## 효과 측정

한 달 뒤 `ETC` 비중을 다시 봅니다. 30% 이하로 떨어지지 않으면 추가 개선이 필요합니다.
""",
    },
    {
        "id": "DOC-205",
        "title": "문의 처리 절차",
        "department": "고객지원팀",
        "type": "매뉴얼 / 정의서",
        "project": None,
        "date": "2025-09-17",
        "systems": ["AIMS"],
        "features": [],
        "participants": [],
        "concepts": ["문의", "운영", "SLA"],
        "body": """## 처리 흐름

```text
접수 -> 담당자 지정 -> 확인 -> 답변 -> 종료
```

## 담당자 지정

문의 목록에서 "가져오기"를 누르면 본인에게 배정됩니다. 자동 배정은 없습니다.

가져온 문의는 본인이 처리합니다. 다른 담당자에게 넘기려면 "재배정"을 사용하고 사유를 남깁니다.

## 응답 목표

| 구분 | 목표 |
| --- | --- |
| 일반 문의 | 영업일 1일 이내 |
| 결제/환불 문의 | 영업일 4시간 이내 |
| 배송 사고 | 영업일 2시간 이내 |

목표를 넘긴 문의는 목록에서 강조 표시됩니다.

## 답변 작성

- 문의 내용을 먼저 다시 확인하세요. 여러 질문이 섞여 있으면 모두 답해야 합니다
- 시스템에서 확인한 사실을 근거로 답합니다. 추측을 단정적으로 쓰지 마세요
- 조치가 필요하면 무엇을 언제까지 하겠다고 명시합니다

## 자주 쓰는 확인 경로

| 문의 유형 | 확인할 것 |
| --- | --- |
| 배송 지연 | 주문 > 배송 탭, 송장 상태와 마지막 동기화 시각 |
| 결제 실패 | 결제 이력, `payment_history` 의 PG 응답 |
| 적립금 누락 | 적립금 이력, 지급 참조키 |
| 쿠폰 사용 불가 | 쿠폰 조건(최소 주문 금액, 유효기간) |
| 로그인 불가 | 고객 상태(휴면/해지), 잠금 여부 |

## 처리 불가 시

우리가 해결할 수 없는 문의(택배사 사정, 판매자 사정)도 답변은 해야 합니다. 상황을 설명하고 예상 일정과 대안을 안내하세요.

"확인 중입니다"만 남기고 종료하지 마세요.

## 종료

답변 후 7일이 지나면 자동 종료됩니다. 추가 질문이 오면 다시 진행 중으로 바뀝니다.

## 에스컬레이션

- 시스템 오류로 판단되면 개발팀 채널에 공유
- 금액 관련 조치가 필요하면 리드 승인
- 동일 유형 문의가 하루 10건 넘게 들어오면 즉시 공유. 장애일 가능성이 있습니다
""",
    },
    {
        "id": "DOC-206",
        "title": "category / product_category 구조 검토",
        "department": "백엔드개발팀",
        "type": "DB 테이블 설계안",
        "project": None,
        "date": "2025-04-16",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": [],
        "concepts": ["계층 구조", "Index", "조회 성능"],
        "body": """## 배경

카테고리가 `parent_id` 로 연결된 트리입니다. 하위 카테고리 전체의 상품을 조회하려면 재귀 조회가 필요해 느립니다.

## 현재 구조

```text
category
  id, parent_id, name, depth, display_order
```

"의류 > 남성 > 상의" 카테고리의 모든 상품을 찾으려면 하위 카테고리를 재귀로 모두 찾아야 합니다.

## 변경안

경로를 문자열로 저장합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | BIGINT | PK |
| parent_id | BIGINT | 직계 부모 |
| path | VARCHAR(255) | 루트부터의 경로 |
| name | VARCHAR(100) | |
| depth | TINYINT | |
| display_order | INT | |
| is_active | BOOLEAN | |

`path` 예시:

```text
/1/
/1/12/
/1/12/103/
```

하위 전체 조회:

```sql
SELECT * FROM category WHERE path LIKE '/1/12/%';
```

앞쪽 와일드카드가 없어 인덱스를 씁니다.

## product_category

상품이 여러 카테고리에 속할 수 있습니다.

| 컬럼 | 설명 |
| --- | --- |
| product_id | FK |
| category_id | FK |
| is_primary | 대표 카테고리 여부 |

```sql
CREATE UNIQUE INDEX uk_product_category ON product_category (product_id, category_id);
CREATE INDEX idx_category_product ON product_category (category_id, product_id);
CREATE INDEX idx_category_path ON category (path);
```

## 대표 카테고리

상품 하나에 대표 카테고리는 하나입니다. 통계와 수수료 산정에 씁니다.

DB 제약으로 "하나만"을 강제하기 어렵습니다. 부분 unique 를 지원하지 않아 애플리케이션에서 보장합니다. 정합성 대조 배치로 검증합니다.

## 카테고리 이동

카테고리를 다른 부모 아래로 옮기면 하위 전체의 `path` 를 갱신해야 합니다.

```sql
UPDATE category
SET path = REPLACE(path, '/1/12/', '/1/15/')
WHERE path LIKE '/1/12/%';
```

카테고리 이동은 드물고(월 1~2회) 대상도 많지 않아 감당 가능합니다.

## 깊이 제한

3단계로 제한합니다. 더 깊어지면 사용자가 탐색하기 어렵고 `path` 길이도 늘어납니다.

운영 화면에서 4단계 생성을 막습니다.
""",
    },
    {
        "id": "DOC-207",
        "title": "상품 목록 조회 API 명세",
        "department": "백엔드개발팀",
        "type": "API 명세서",
        "project": None,
        "date": "2025-05-21",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": [],
        "concepts": ["페이징", "정렬", "캐시"],
        "body": """## Endpoint

```http
GET /products
```

인증이 필요 없습니다.

## 파라미터

| 파라미터 | 필수 | 설명 |
| --- | --- | --- |
| categoryId | N | 하위 카테고리 포함 |
| keyword | N | 상품명 검색 (2자 이상) |
| minPrice, maxPrice | N | 가격 범위 |
| sort | N | RECENT / PRICE_ASC / PRICE_DESC / POPULAR |
| includeSoldOut | N | 기본 true |
| page, size | N | 기본 0, 20. size 최대 100 |

`categoryId` 와 `keyword` 중 하나는 있어야 합니다. 전체 목록 조회는 지원하지 않습니다.

## Response

```json
{
  "content": [
    {
      "productId": 88201,
      "name": "무선 이어폰",
      "price": 12000,
      "discountPrice": 10800,
      "thumbnailUrl": "https://cdn.example.com/p/88201.jpg",
      "rating": 4.6,
      "reviewCount": 214,
      "soldOut": false
    }
  ],
  "totalElements": 1284,
  "page": 0,
  "size": 20
}
```

`soldOut` 은 재고 기준으로 계산된 값입니다. 상품 상태와 별개입니다.

## 정렬

`POPULAR` 는 최근 7일 판매량 기준입니다. 집계 배치가 하루 1회 갱신하므로 실시간이 아닙니다.

품절 상품은 어떤 정렬이든 뒤로 밀립니다.

## 캐시

카테고리 목록 조회는 60초 캐시합니다. 검색어가 있는 조회는 캐시하지 않습니다.

가격과 재고는 캐시 대상에서 제외되어 매번 조회합니다. 목록에 표시된 가격과 실제 주문 시 가격이 다르면 주문이 거부되므로 정확해야 합니다.

## Error Code

| 코드 | HTTP | 설명 |
| --- | --- | --- |
| PRODUCT_1001 | 400 | 조회 조건 없음 |
| PRODUCT_1002 | 400 | 검색어 2자 미만 |
| PRODUCT_1003 | 400 | size 상한 초과 |
| PRODUCT_1004 | 404 | 존재하지 않는 카테고리 |

## 주의사항

`page` 가 커지면 응답이 느려집니다. 100페이지 이상은 거부됩니다. 깊은 페이징이 필요하면 정렬 조건을 좁혀서 사용하세요.
""",
    },
    {
        "id": "DOC-208",
        "title": "상품 검색 개선 방향 논의",
        "department": "백엔드개발팀",
        "type": "개발자 회의록",
        "project": None,
        "date": "2026-05-20",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": ["배현우", "최유진"],
        "concepts": ["검색", "형태소", "정확도"],
        "body": """## 논의 배경

상품 검색 정확도에 대한 불만이 있습니다. "무선이어폰"으로 검색하면 결과가 나오는데 "무선 이어폰"은 결과가 적습니다.

## 현재 방식

`LIKE '%keyword%'` 로 상품명을 검색합니다. 공백까지 그대로 매칭하므로 띄어쓰기가 다르면 안 잡힙니다.

## 확인된 문제

최유진님이 정리한 사례입니다.

| 검색어 | 결과 수 | 기대 |
| --- | --- | --- |
| 무선이어폰 | 42 | |
| 무선 이어폰 | 8 | 42 |
| 이어폰 무선 | 0 | 42 |
| 블루투스이어폰 | 3 | 유사 상품 포함 |

어순이 바뀌면 아예 안 나옵니다.

## 검토안

**1. 공백 제거 후 매칭**

검색어와 상품명 모두 공백을 제거하고 비교합니다. 간단하고 첫 두 사례를 해결합니다. 어순 문제는 남습니다.

**2. 전문 검색 인덱스 (n-gram)**

회원 이름 검색에서 쓴 방식입니다. 어순 문제도 일부 해결됩니다.

**3. 검색 엔진 도입**

형태소 분석, 동의어, 오타 보정이 가능합니다. 인프라와 운영 비용이 큽니다.

## 쟁점

배현우님은 3안이 근본 해결이지만 지금 규모에서 과하다고 봤습니다. 상품 수가 12만 건이고 검색 트래픽도 크지 않습니다.

최유진님은 검색이 상품 발견의 주요 경로라 투자 가치가 있다고 했습니다. 다만 이번 분기에 할 수 있는 것부터 하자는 데 동의했습니다.

## 결정 사항

- 1차로 **2안(n-gram 전문 인덱스)** 을 적용합니다
- 검색어와 상품명 정규화(공백, 대소문자, 특수문자)를 함께 적용합니다
- 동의어 사전은 수동으로 소규모 관리합니다 (블루투스=무선 등)
- 3안은 검색 지표를 모은 뒤 다음 분기에 재검토합니다

## 지표

무엇을 개선했는지 알려면 지표가 필요합니다.

- 검색 후 상품 클릭률
- 결과 0건 검색어 목록
- 검색 후 이탈률

배현우님이 수집 파이프라인을 만들기로 했습니다.

## Action Item

- 배현우님: 검색 지표 수집
- 최유진님: 검색어 정규화 규칙 정리
""",
    },
    {
        "id": "DOC-209",
        "title": "특정 검색어에서 상품이 조회되지 않음",
        "department": "백엔드개발팀",
        "type": "이슈 정리",
        "project": None,
        "date": "2026-05-27",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": [],
        "concepts": ["검색", "인덱스", "정규화"],
        "body": """## 현상

전문 인덱스 적용 후 일부 검색어에서 결과가 나오지 않습니다. 이전(LIKE 방식)에는 나오던 상품입니다.

## 재현

| 검색어 | LIKE 결과 | 전문 인덱스 결과 |
| --- | --- | --- |
| 이어폰 | 42 | 42 |
| 폰 | 118 | 0 |
| AirPods | 6 | 0 |
| 5V | 14 | 0 |

## 분석

두 가지 원인이 있습니다.

**1. n-gram 최소 토큰 길이**

MySQL n-gram 파서의 기본 토큰 크기가 2입니다. 1글자 검색어는 토큰이 만들어지지 않아 결과가 없습니다.

"폰" 같은 1글자 검색은 원래도 결과가 너무 많아 유용하지 않지만, 0건이 나오는 것은 문제입니다.

**2. 불용어 목록**

MySQL 전문 검색에는 기본 불용어(stopword) 목록이 있습니다. 영문 관사와 짧은 단어가 포함되어 있습니다.

`AirPods` 는 불용어가 아닌데도 안 나옵니다. 확인해 보니 전문 인덱스가 대소문자를 구분하지 않는 것과 별개로, 상품명에 `Airpods` 로 저장된 것을 `AirPods` 로 검색하면서 n-gram 토큰이 달라졌습니다. 실제로는 콜레이션 설정 문제였습니다.

`5V` 는 숫자와 영문이 섞여 토큰 분리가 예상과 다르게 일어납니다.

## 조치

- 1글자 검색어는 전문 인덱스 대신 `LIKE 'keyword%'` 로 폴백 (접두 일치, 인덱스 사용 가능)
- 테이블 콜레이션을 대소문자 무시로 통일
- 기본 불용어 목록을 비활성화. 상품명에는 불용어 개념이 맞지 않음
- 검색어 정규화 시 영문/숫자 경계에 공백을 넣지 않도록 수정

## 남은 문제

`5V` 같은 영숫자 혼합 검색어는 여전히 정확도가 떨어집니다. n-gram 파서 특성이라 설정으로 해결하기 어렵습니다.

결과 0건 검색어를 수집하고 있으므로, 목록을 보고 패턴이 반복되면 별도 대응을 검토합니다.

## 배운 점

전문 인덱스로 바꾸면서 "이전보다 좋아진다"고 가정했습니다. 실제로는 이전에 되던 것이 안 되는 경우가 생겼습니다.

검색 방식을 바꿀 때는 기존 검색어 상위 목록으로 회귀 테스트를 해야 합니다. 지금은 지표를 모으고 있어서 다음부터는 가능합니다.
""",
    },
    {
        "id": "DOC-210",
        "title": "검색 개선 1차 결과",
        "department": "데이터팀",
        "type": "프로젝트 진행상황",
        "project": None,
        "date": "2026-06-24",
        "systems": ["ORBIT"],
        "features": ["상품"],
        "participants": [],
        "concepts": ["검색", "지표", "우선순위"],
        "body": """## 적용 내용

- 상품명 n-gram 전문 인덱스
- 검색어 정규화 (공백, 대소문자, 특수문자)
- 1글자 검색어 접두 일치 폴백
- 동의어 사전 42쌍 (수동 관리)

## 지표 변화

5월 20일 적용 전후 4주 비교입니다.

| 지표 | 적용 전 | 적용 후 |
| --- | --- | --- |
| 검색 후 상품 클릭률 | 31.2% | 44.8% |
| 결과 0건 비율 | 18.4% | 7.1% |
| 검색 후 이탈률 | 42.1% | 33.6% |
| 검색 응답 p99 | 1,240ms | 180ms |

## 결과 0건 검색어 상위

여전히 0건이 나오는 검색어를 분류했습니다.

| 유형 | 비중 | 예 |
| --- | --- | --- |
| 취급하지 않는 상품 | 41% | 브랜드명 오입력 |
| 오타 | 27% | "무넌 이어폰" |
| 영숫자 혼합 | 14% | 규격 표기 |
| 동의어 미등록 | 11% | |
| 기타 | 7% | |

오타가 27% 입니다. 오타 보정이 다음 개선의 가장 큰 효과가 있을 것으로 보입니다.

## 동의어 사전 운영

42쌍을 수동으로 등록했습니다. 결과 0건 목록을 주 1회 확인하며 추가합니다.

수동 관리라 늘어나면 부담입니다. 100쌍을 넘으면 관리 방식을 다시 봐야 합니다.

## 다음 단계

**우선순위 1: 오타 보정**

편집 거리 기반 보정을 검토합니다. "혹시 이것을 찾으셨나요" 형태로 제안합니다.

**우선순위 2: 검색 엔진 도입 재검토**

오타 보정과 형태소 분석을 직접 구현하면 결국 검색 엔진의 기능을 다시 만드는 셈입니다. 이번 지표로 검색의 가치가 확인됐으므로 도입을 다시 논의합니다.

## 미결

검색 엔진 도입은 인프라 비용과 운영 부담이 있어 결정이 필요합니다. 다음 분기 계획에 안건으로 올립니다.
""",
    },
]
