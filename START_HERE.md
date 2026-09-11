# 내부망 작업 매뉴얼

압축을 푼 뒤 이 문서부터 읽는다. 순서대로 따라가면 용어 사전이 나온다.

- 반입 꾸러미를 만드는 쪽 절차는 `DEPLOY_INTERNAL.md` 에 있다. 여기서는 다루지 않는다.
- 설정 항목의 자세한 설명은 `config.internal.yaml` 의 주석에 있다.

**전체 소요 (실측 기준)**

| 단계 | 시간 | 무엇이 시간을 쓰나 |
|---|---|---|
| 설치·설정·점검 | 30분 | 대기 시간 |
| 첫 결과 확인 | 10분 | 파이프라인 1회 실행 |
| 사내 용어 반영 (5장) | **2~4시간** | **사람이 용어 목록을 읽고 판단하는 시간** |
| LLM 검증 | 후보 수에 비례 | 후보 700쌍이면 약 30회 요청 |

파이프라인 자체는 빠르다. 문서 307개·문장 10,821개 기준 **1회 15~20초**다.
5장의 시간은 기계가 아니라 사람이 쓰는 시간이며, 한 사이클(실행 + 검토 + 사전 추가)에
20~40분씩 3~5회 반복한다.

문서가 수천 건이면 실행 시간이 몇 분 단위로 늘지만(형태소 분석이 대부분),
사람이 보는 분량은 문서 수가 아니라 **용어 수**에 비례하므로 크게 달라지지 않는다.

---

## 0. 준비물 확인

| 항목 | 확인 명령 | 필요 조건 |
|---|---|---|
| Python | `python --version` | 3.9 ~ 3.13 |
| CPU | `python -c "import platform; print(platform.machine())"` | `AMD64` |
| Java | `java -version` | 8 이상 (17 권장), **64bit** |

`java -version` 출력에 `64-Bit Server VM` 이 보여야 한다. 32bit JDK 면 JVM 로드가 실패한다.

확보해 둘 정보:

| 항목 | 예시 | 어디서 |
|---|---|---|
| Confluence 주소 | `https://confluence.company.co.kr` | 브라우저 주소창 |
| 개인 액세스 토큰(PAT) | | Confluence 프로필 → Personal Access Tokens |
| 대상 space key | `TEAMSPACE` | space URL 의 `/display/XXX/` |
| 사내 LLM 엔드포인트 | `http://llm.company.co.kr/v1/chat/completions` | 담당 부서 |
| LLM 요청 규격 | OpenAI 호환 여부 | 담당 부서 |

---

## 1. 설치 (약 10분)

```bat
cd term-dictionary

python -m venv .venv
.venv\Scripts\activate.bat

python -m pip install --no-index --find-links wheels --upgrade pip
python -m pip install --no-index --find-links wheels -r requirements-internal.txt
```

`--no-index` 라 인터넷을 보지 않는다. `wheels\` 폴더 안에서만 해결한다.

`JAVA_HOME` 이 설정돼 있지 않으면:

```bat
setx JAVA_HOME "C:\Program Files\Java\jdk-17"
```

설정 후 **명령 프롬프트를 새로 열어야** 반영된다.

### 선택: 자체 테스트

`wheels-optional\` 이 함께 왔다면 반입이 온전한지 확인할 수 있다.

```bat
python -m pip install --no-index --find-links wheels-optional pytest
python -m pytest -q
```

178개가 통과하면 코드는 정상이다.

---

## 2. 설정 (약 10분)

비밀값은 환경변수로 둔다. `config.internal.yaml` 이 `${...}` 로 참조한다.

```bat
set CONFLUENCE_BASE_URL=https://confluence.company.co.kr
set CONFLUENCE_API_TOKEN=<개인 액세스 토큰>
set CONFLUENCE_EMAIL=
set INTERNAL_LLM_URL=http://llm.company.co.kr/v1/chat/completions
set INTERNAL_LLM_MODEL=<모델 이름>
set INTERNAL_LLM_API_KEY=<필요하면>
```

매번 입력하기 번거로우면 `run_internal.bat` 안의 주석 처리된 `set` 줄을 채운다.
또는 시스템 환경변수로 등록한다(`setx`).

`config.internal.yaml` 에서 **space key 만** 실제 값으로 바꾼다.

```yaml
confluence:
  space_keys: ["TEAMSPACE"]     # ← 여기
```

나머지 기본값은 내부망을 전제로 이미 맞춰져 있다.

| 키 | 기본값 | 왜 이 값인가 |
|---|---|---|
| `confluence.api_version` | `v1` | 구버전(Server/DC)은 `/rest/api/content` 를 쓴다 |
| `confluence.auth_type` | `bearer` | PAT 를 `Authorization: Bearer` 로 보낸다 |
| `cleaning.output_format` | `markdown` | HTML 본문의 제목·표 구조를 남긴다 |
| `fasttext.enabled` | `false` | gensim 없이 돌린다 |
| `llm_validation.provider` | `http` | 사내 LLM 서버 |

### Confluence 인증 방식

사내 Confluence(Server/DC)는 보통 **개인 액세스 토큰(PAT)** 을 씁니다.
Postman 에서 `Authorization: Bearer <토큰>` 으로 보내는 그 방식입니다.

```yaml
confluence:
  auth_type: "bearer"     # PAT -> Authorization: Bearer <토큰>
```

`auth_type` 을 지정하지 않으면 `email` 값이 있는지로 방식을 추측하는데,
PAT 를 쓰면서 `CONFLUENCE_EMAIL` 을 채워 두면 Basic 인증으로 바뀌어 401 이 납니다.
**`bearer` 로 명시하는 편이 안전합니다.**

Cloud API 토큰이나 ID/PW 를 쓴다면 `basic` 으로 두고 `email` 을 채웁니다.

```yaml
confluence:
  auth_type: "basic"
  email: "${CONFLUENCE_EMAIL}"
```

### 사내 LLM 규격 맞추기

엔드포인트 경로를 보면 대체로 알 수 있습니다.

| 서버 경로 | `request_format` | 비고 |
|---|---|---|
| `/v1/chat/completions` | `openai` | 가장 흔함 |
| `/v1/messages` | **`anthropic`** | vLLM 의 Anthropic 호환 포함 |
| 사내 자체 규격 | `plain` | 키 이름을 직접 지정 |

`/v1/messages` 에 `openai` 형식으로 보내면 이런 400 이 납니다.

```
Input should be 'user' or 'assistant' ... 'input': 'system'
```

Anthropic 규격은 `system` 을 messages 배열이 아니라 **최상위 필드**로 받기 때문입니다.
`request_format: "anthropic"` 으로 바꾸면 해결됩니다.

system 역할 자체를 받지 않는 서버라면:

```yaml
llm_validation:
  merge_system_into_user: true
```

### 사내 LLM 이 OpenAI 호환이 아니면

```yaml
llm_validation:
  request_format: "plain"        # {"model", "system", "prompt"} 형태로 보낸다
  system_key: "instruction"      # 사내 규격의 키 이름으로 바꾼다
  prompt_key: "input"
  response_path: "data.text"     # 응답에서 본문을 꺼낼 경로
  auth_header: "X-API-KEY"       # 인증 헤더가 다르면
  auth_prefix: ""
```

**사내 LLM 이 tool-use(function calling)를 지원하지 않아도 된다.**
도구 스키마를 프롬프트에 실어 보내고 응답에서 JSON 을 파싱한다.
코드펜스나 앞뒤 설명이 섞여 나와도 처리한다.

---

## 3. 환경 점검 (반드시 먼저)

```bat
python tools\check_environment.py -c config.internal.yaml
```

파이썬 버전, 패키지, Java 버전·비트수, JVM 라이브러리, Komoran 실동작, 설정,
쓰기 권한을 한 번에 확인한다.

서버 연결까지 보려면:

```bat
python tools\check_environment.py -c config.internal.yaml --connect
```

Confluence 에서 페이지 1건을 읽고, 사내 LLM 에 `{"ok": true}` 를 요청해 본다.
**여기서 `[실패]` 가 하나도 없어야 다음으로 간다.** 파이프라인을 돌리며
하나씩 실패를 만나는 것보다 빠르다.

---

## 4. 첫 실행 — 단계별로 나눠 돌린다

> **`-c` 위치**: `python -m app.main -c 설정파일 명령` 순서로 쓴다.
> 서브커맨드 뒤에 써도(`... collect -c config.internal.yaml`) 동작하지만,
> 앞에 두는 편이 표준이다.

한 번에 전체를 돌리지 않는다. LLM 비용을 쓰기 전에 형태소 분석 품질을 먼저 본다.

### 4-1. 수집만

```bat
python -m app.main -c config.internal.yaml collect
```

문서 수가 예상과 맞는지 확인한다. 0건이면 space key 나 계정 권한 문제다.

### 4-2. 후보 생성까지 (LLM 호출 없음)

```bat
python -m app.main -c config.internal.yaml build --skip-collect --skip-llm
```

`--skip-collect` 는 이미 받아 둔 문서를 쓴다. 다시 수집하지 않는다.

### 4-3. 용어 시트 확인 ← **여기가 핵심**

```bat
python tools\review_sheet.py export -c config.internal.yaml
```

`data\review\terms.csv` 를 연다. 빈도 높은 순이다. 상위 100개를 훑으며 본다.

- 사내 용어가 **쪼개져** 있지 않은가 (`재고관리` → `재고` + `관리`)
- 조사가 **붙어** 있지 않은가 (`설비를`, `공정에서`)
- 사람 이름·제품명이 온전한가

쪼개진 게 많으면 그 상태로 LLM 을 돌려도 결과가 나쁘다. 다음 단계로 간다.

---

## 5. 사용자 사전 보강 — 여기에 시간을 쓴다

`data\userdic.txt` 에 사내 용어를 넣으면 형태소 분석이 그 단어를 통째로 인식한다.
**이 작업이 결과 품질을 가장 크게 좌우한다.**

### 형식

```
재고관리	NNP
공정 이상	NNP
설비 점검	NNP
```

- 구분자는 **탭** 문자다. 공백이 아니다.
- 품사는 고유명사면 `NNP`, 일반명사면 `NNG`. 애매하면 `NNP`.
- 여러 어절로 된 용어도 한 줄에 쓴다.
- 파일 인코딩은 **UTF-8**. 메모장이면 저장 시 인코딩을 UTF-8 로 지정한다.

### 후보를 어디서 얻나

파이프라인이 자동으로 뽑아 둔다.

```
data\output\userdic_candidates.txt
```

복합어로 자주 붙어 나온 토큰 목록이다. 이 중 실제 사내 용어를 골라
`data\userdic.txt` 에 옮긴다. `terms.csv` 에서 쪼개져 보이는 것도 함께 넣는다.

### 반복

```bat
REM 사전 수정 후 다시
python -m app.main -c config.internal.yaml build --skip-collect --skip-llm
python tools\review_sheet.py export -c config.internal.yaml
```

`terms.csv` 상위 용어가 대부분 온전해질 때까지 반복한다.
보통 **3~5회** 돌린다. 한 번에 수십 개씩 추가하는 편이 빠르다.

한 사이클의 실제 배분(문서 307개 기준):

| | 시간 |
|---|---|
| 파이프라인 실행 | 15~20초 |
| 시트 내보내기 | 2~3초 |
| **상위 100개 검토 + 사전 추가** | **20~40분** |

기계를 기다리는 시간은 없다. 전부 사람이 읽고 판단하는 시간이다.
그래서 한 번에 많이 모아서 넣는 편이 낫다 — 10개씩 넣고 다시 돌리면
읽는 시간만 반복된다.

---

## 6. LLM 검증

용어가 제대로 잡히면 그때 LLM 을 붙인다.

```bat
python -m app.main -c config.internal.yaml build --skip-collect
```

후보 수에 비례해 요청이 나간다. 후보 700쌍이면 배치 24개씩 약 30회 요청이다.
먼저 후보 수를 확인하고 시작한다 — `data\review\relations.csv` 의 행 수다.

### 사내 LLM 을 직접 호출할 수 없다면

망 분리가 더 엄격해 파이프라인에서 LLM 서버로 못 붙는 경우, 파일로 중계한다.

```bat
REM 1) 프롬프트 파일 생성
python tools\llm_bridge.py export -c config.internal.yaml

REM 2) data\output\llm\prompts\*.txt 를 LLM 에 넣고
REM    응답 JSON 을 data\output\llm\answers\ 에 같은 이름(.json)으로 저장

REM 3) 반영
python tools\llm_bridge.py apply -c config.internal.yaml
```

Enum 강제, 증거 context id 검증, 근거 부족 시 확신도 하향 같은 방어 로직이
그대로 적용된다.

---

## 7. 결과 확인과 검수

| 경로 | 내용 |
|---|---|
| `data\output\term_dictionary.json` | 최종 용어 사전 |
| `data\review\relations.csv` | 관계 검수 시트 (우선순위 높은 순) |
| `data\review\terms.csv` | 용어 검수 시트 |
| `data\output\review_queue.json` | 사람 판단이 필요한 관계 |
| `data\output\pipeline.log` | 실행 로그 |

### relations.csv 읽는 법

| 열 | 뜻 |
|---|---|
| `상태` | `ACTIVE` = 자동 승인, `REVIEW` = 사람 판단 필요, `REJECTED` = 기각 |
| `관계타입` | `EXACT_ALIAS`(같은 말) / `BROADER`·`NARROWER`(상하위) / `RELATED`(관련) |
| `경로` | 어느 방법이 이 쌍을 찾았는지 |
| `확장가능` | 검색 확장에 써도 되는 방향인지 |
| `근거` | LLM 이 그렇게 판단한 이유 |

**위에서부터 200행 정도만 보면 된다.** 우선순위 순으로 정렬돼 있다.

`ACTIVE` 는 검수 없이 사전에 실린 것이다. 여기가 틀리면 검색이 오염되므로
**ACTIVE 는 전부 눈으로 확인한다.** 보통 10~20건이라 금방 본다.

### 검수 반영

`제거` 열에 `X` 를 넣고 저장한 뒤:

```bat
python tools\review_sheet.py apply -c config.internal.yaml
```

제거 이력이 `data\review\removed_terms.txt` 와 `removed_relations.txt` 에 쌓여
**다음 실행부터 자동으로 빠진다.** 같은 것을 두 번 지울 필요가 없다.

---

## 8. 결과가 기대에 못 미칠 때

먼저 `data\review\terms.csv` 를 다시 본다. 용어가 제대로 안 잡혀 있으면
어떤 설정을 바꿔도 소용없다. 5장으로 돌아간다.

용어는 괜찮은데 관계가 이상하면 `config.internal.yaml` 의 아래 값을 조정한다.

| 증상 | 바꿀 값 | 방향 |
|---|---|---|
| 후보가 너무 적다 | `candidate.min_term_frequency` | 5 → 3 |
| 후보가 너무 많다 | `candidate.max_candidates_per_term` | 12 → 8 |
| 잡음이 많다 | `candidate.require_corroboration` | `true` 유지 |
| 정상 용어가 빠진다 | `candidate.min_sentence_ratio` | 0.05 → 0 |

**한 번에 하나씩만 바꾸고 결과를 비교한다.** 두 개를 동시에 바꾸면 무엇이
효과를 냈는지 알 수 없다.

### 정답 세트를 만들어 비교하기 (권장)

감으로 조정하지 않으려면 사내 정답 세트를 만든다.

```bat
REM 층화 표본 250쌍을 뽑는다
python tools\gold_set.py sample -c config.internal.yaml --size 250

REM data\review\gold_set.csv 의 '라벨' 열을 채운다
REM   SYNONYM / DIRECTIONAL / RELATED / UNRELATED 중 하나

REM 설정을 바꿀 때마다 같은 기준으로 비교한다
python tools\gold_set.py evaluate -c config.internal.yaml
```

후보 recall, ACTIVE 정밀도, 경로별 유의미 비율을 수치로 보여준다.
**한 번 만들어 두면 이후 모든 튜닝 판단이 근거를 갖는다.**

---

## 9. 정기 운영

문서가 늘거나 바뀌면 다시 돌린다. 변경된 문서만 다시 받는다.

```bat
python -m app.main -c config.internal.yaml build
```

실행 이력:

```bat
python -m app.main -c config.internal.yaml runs
```

DB 상태만으로 사전 JSON 을 다시 만들려면(재수집·재학습 없음):

```bat
python -m app.main -c config.internal.yaml rebuild
```

---

## 10. 자주 나는 문제

| 증상 | 원인 | 조치 |
|---|---|---|
| `UnicodeEncodeError` | 콘솔이 cp949 | `run_internal.bat` 사용, 또는 `set PYTHONUTF8=1` |
| `KomoranUnavailableError` | JDK 없음 / `JAVA_HOME` 미설정 | JDK 확인 후 `setx JAVA_HOME`, 프롬프트 새로 열기 |
| JVM 로드 실패 | 32bit JDK + 64bit Python | 64bit JDK 로 교체 |
| `SSLError` | 사내 사설 CA | `confluence.verify_ssl` 에 CA 번들 경로 지정 |
| Confluence 404 | v2 경로로 요청 | `api_version: v1` 확인 |
| Confluence 401 | 인증 방식이 다름 | `auth_type: bearer` 확인. 오류 메시지가 어느 방식으로 보냈는지 알려준다 |
| 페이지 0건 | space key 오타 / 권한 없음 | `--connect` 점검, space key 대문자 확인 |
| LLM 응답 파싱 실패 | 응답 경로가 다름 | 실제 응답 JSON 을 보고 `response_path` 수정 |
| 용어가 다 쪼개짐 | 사용자 사전 부족 | 5장 참고 |
| 사전에 이상한 게 실림 | ACTIVE 정책이 넓음 | `relation_policy.active_relation_types` 축소 |

---

## 11. 미리 알아 둘 것

**사내 용어는 처음부터 다시 잡아야 한다.**
`data\userdic.txt` 에 들어 있는 147개 항목은 개발 중 쓴 가상 회사(NexBridge) 기준이다.
실제 사내 용어와 다르므로 5장의 반복 작업이 반드시 필요하다.
남겨 둔 이유는 형식 예시로 쓰기 위해서다. 지우고 새로 채워도 된다.

**설정 임계값도 다시 잡아야 한다.**
`min_term_frequency: 5`, `min_sentence_ratio: 0.05` 같은 값은 문서 300개 기준으로
맞춘 것이다. 사내 문서가 수천 건이면 달라진다. 8장의 gold set 방식을 권한다.

**분포 기반 방법(FastText)은 기본으로 꺼져 있다.**
개발 중 측정에서 문서 300개 규모로는 기여가 거의 없었다(유의미 비율 1.9%).
사내 문서가 훨씬 많으면 켜 볼 가치가 있다. gensim 설치가 필요하다.

```yaml
fasttext:
  enabled: true
context_profile:
  enabled: true
```

```bat
python -m pip install --no-index --find-links wheels-optional\gensim gensim
```

Python 3.10~3.12 에서만 설치된다.

---

## 12. 체크리스트

설치
- [ ] `python --version` 이 3.9~3.13
- [ ] `java -version` 이 8 이상, 64bit
- [ ] `pip install` 이 오류 없이 끝났다
- [ ] `check_environment.py` 에 `[실패]` 가 없다

설정
- [ ] `space_keys` 를 실제 값으로 바꿨다
- [ ] 환경변수 5개를 설정했다
- [ ] `--connect` 점검에서 Confluence 와 LLM 이 모두 통과했다

첫 결과
- [ ] `collect` 로 받은 문서 수가 예상과 맞다
- [ ] `terms.csv` 상위 100개 용어가 온전하다
- [ ] `userdic.txt` 보강을 3회 이상 반복했다

품질
- [ ] `relations.csv` 의 `ACTIVE` 를 전부 눈으로 확인했다
- [ ] gold set 을 만들어 기준선을 잡았다
