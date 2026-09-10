# 내부망 반입 절차 (반입 준비하는 쪽)

이 문서는 **외부망에서 반입 꾸러미를 만드는 사람**을 위한 것이다.

> **내부망에서 압축을 푼 뒤라면 `START_HERE.md` 를 읽는다.**
> 설치·설정·실행·품질 개선까지의 작업 매뉴얼이 거기 있다.

---

## 0. 먼저 확인할 것

내부망 PC 에서 아래 두 가지를 확인하고 적어 둔다. **wheel 은 이 값에 맞춰 받아야 한다.**

```
python --version          → 예: Python 3.11.9
python -c "import platform; print(platform.machine())"   → 예: AMD64
```

Confluence 와 사내 LLM 정보도 미리 확보한다.

| 항목 | 예시 | 확인 방법 |
|---|---|---|
| Confluence 주소 | `https://confluence.company.co.kr` | 브라우저 주소창 |
| Confluence 버전 | Server / Data Center | 우측 상단 톱니 → 일반 구성 |
| 개인 액세스 토큰(PAT) | | 프로필 → Personal Access Tokens |
| 대상 space key | `TEAMSPACE` | space URL 의 `/display/XXX/` |
| 사내 LLM 엔드포인트 | `http://llm.company.co.kr/v1/chat/completions` | 담당 부서 |
| LLM 요청 규격 | OpenAI 호환 여부 | 담당 부서 |

---

## 1. 외부망에서 반입 꾸러미 만들기

### 1-1. 소스 준비

```bash
# 불필요한 것은 빼고 압축한다
tar --exclude=.venv --exclude=data --exclude=.git --exclude=__pycache__ \
    --exclude=corpus -czf term-dictionary.tar.gz .
```

`corpus/` 는 테스트용 가상 회사 문서라 반입할 필요가 없다.
`data/userdic.txt` 는 **반입한다** (사용자 사전은 품질에 직접 영향을 준다).

### 1-2. wheel — 이미 준비되어 있다

`wheels/` 폴더에 **Windows 64bit / Python 3.9~3.13 전부**를 미리 담아 뒀다.
따로 받을 필요가 없다. 폴더째 반입하면 된다.

| 폴더 | 내용 | 크기 |
|---|---|---|
| `wheels/` | 필수 패키지 (5개 파이썬 버전) | 약 105MB |
| `wheels-optional/` | pytest, python-dotenv, gensim | 약 247MB |

파이썬 버전을 몰라도 된다. 파일 이름에 `cp39`, `cp311` 처럼 버전이 박혀 있어
pip 이 알아서 맞는 것을 고른다.

다른 버전이나 플랫폼이 필요하면 외부망에서 다시 받을 수 있다:

```bash
python tools/download_wheels.py --python-version 311 --platform win_amd64
```

### 1-3. JDK — 이것만 직접 준비해야 한다

Komoran 은 JVM 위에서 돈다. pip 로 설치되지 않으므로 JDK 는 별도로 있어야 한다.

**JDK 17(LTS)로 이 프로젝트 전체를 개발·검증했다.** 사내에 JDK 17 이 있으면
그대로 쓰면 된다. JDK 8 이상이면 동작하지만 17 을 권장한다.

내부망 PC 에서 확인:

```bat
java -version
python -c "import platform; print(platform.architecture()[0])"
```

두 가지를 함께 본다.

| 확인 | 기준 | 어긋나면 |
|---|---|---|
| Java 버전 | 8 이상 (17 권장) | JDK 설치 파일 반입 필요 |
| **비트수** | Java 와 Python 이 **둘 다 64bit** | 32bit JDK 면 JVM 로드가 실패한다 |

`java -version` 출력에 `64-Bit Server VM` 이 보이면 64bit 다.
JDK 가 아니라 JRE 만 있어도 동작하지만, `JAVA_HOME` 은 `jvm.dll` 이 있는
최상위 폴더를 가리켜야 한다 (`%JAVA_HOME%\bin\server\jvm.dll`).

`tools\check_environment.py` 가 위 항목을 자동으로 점검한다.

### 1-4. 반입 목록

```
term-dictionary.tar.gz      소스 (wheels/ 포함)
jdk-17-windows-x64.msi      JDK — 직접 준비, 내부망에 없을 때만
```

`wheels/` 와 `wheels-optional/` 은 소스 압축에 이미 들어 있다.

---

## 2. 내부망에서 설치

```bat
REM 1) 압축 해제 후 해당 폴더에서
python -m venv .venv
.venv\Scripts\activate.bat

REM 2) 오프라인 설치
python -m pip install --no-index --find-links wheels --upgrade pip
python -m pip install --no-index --find-links wheels -r requirements-internal.txt

REM 3) JDK 경로 설정 (설치 위치에 맞게)
setx JAVA_HOME "C:\Program Files\Java\jdk-17"
```

---

## 3. 설정

`config.internal.yaml` 을 열어 채운다. 비밀값은 환경변수로 두는 편이 낫다.

```bat
set CONFLUENCE_BASE_URL=https://confluence.company.co.kr
set CONFLUENCE_API_TOKEN=<개인 액세스 토큰>
set CONFLUENCE_EMAIL=
set INTERNAL_LLM_URL=http://llm.company.co.kr/v1/chat/completions
set INTERNAL_LLM_MODEL=<모델 이름>
set INTERNAL_LLM_API_KEY=<필요하면>
```

`config.internal.yaml` 에서 최소한 아래를 확인한다.

| 키 | 값 | 이유 |
|---|---|---|
| `confluence.api_version` | `v1` | 구버전(Server/DC)은 `/rest/api/content` 를 쓴다 |
| `confluence.space_keys` | `["TEAMSPACE"]` | v1 은 space key 를 그대로 쓴다 |
| `cleaning.output_format` | `markdown` | HTML 본문의 제목·표 구조를 남긴다 |
| `fasttext.enabled` | `false` | gensim 없이 돌린다 |
| `llm_validation.provider` | `http` | 사내 LLM 서버 |

### 사내 LLM 규격이 OpenAI 호환이 아니면

```yaml
llm_validation:
  request_format: "plain"           # {"model", "system", "prompt", ...} 로 보낸다
  system_key: "instruction"         # 사내 규격의 키 이름
  prompt_key: "input"
  response_path: "data.text"        # 응답에서 본문을 꺼낼 경로
  auth_header: "X-API-KEY"          # 인증 헤더가 다르면
  auth_prefix: ""
```

사내 LLM 이 tool-use(function calling)를 지원하지 않아도 된다.
도구 스키마를 프롬프트에 실어 보내고 응답에서 JSON 을 파싱한다.
코드펜스나 앞뒤 설명이 섞여 나와도 처리한다.

---

## 4. 환경 점검 (가장 먼저 할 것)

```bat
python tools\check_environment.py -c config.internal.yaml
```

파이썬 버전, 패키지, Java, Komoran 동작, 설정, 쓰기 권한을 한 번에 확인한다.
서버 연결까지 보려면:

```bat
python tools\check_environment.py -c config.internal.yaml --connect
```

Confluence 에서 페이지 1건을 읽고, 사내 LLM 에 `{"ok": true}` 를 요청해 본다.

자체 테스트도 돌려 본다(선택):

```bat
python -m pytest -q
```

---

## 5. 실행

```bat
run_internal.bat
```

또는 단계별로:

```bat
REM 수집만
python -m app.main collect -c config.internal.yaml

REM 후보 생성까지 (LLM 호출 없음) — 먼저 이걸로 품질을 본다
python -m app.main build -c config.internal.yaml --skip-llm

REM 전체
python -m app.main build -c config.internal.yaml
```

### 권장 순서

1. `--skip-llm` 으로 돌려 `data/review/terms.csv` 를 본다.
2. 사내 용어에 맞게 `data/userdic.txt` 를 보강한다.
   (`data/output/userdic_candidates.txt` 에 후보가 나온다)
3. 다시 `--skip-llm` 으로 돌려 토큰이 제대로 잡히는지 확인한다.
4. 그다음 LLM 검증까지 돌린다.

형태소 분석이 어긋난 상태로 LLM 을 돌리면 비용만 쓰고 결과가 나쁘다.

---

## 6. 결과물

| 경로 | 내용 |
|---|---|
| `data/output/term_dictionary.json` | 최종 용어 사전 |
| `data/review/relations.csv` | 관계 검수 시트 |
| `data/review/terms.csv` | 용어 검수 시트 |
| `data/output/pipeline.log` | 실행 로그 |

검수 반영:

```bat
REM 시트의 '제거' 열에 X 를 넣고 저장한 뒤
python tools\review_sheet.py apply -c config.internal.yaml
```

제거 이력은 `data/review/removed_terms.txt` 에 쌓여 다음 실행부터 자동 반영된다.

---

## 7. 사내 LLM 을 직접 호출할 수 없을 때

망 분리가 더 엄격해 파이프라인에서 LLM 서버로 직접 못 붙는 경우, 파일로 중계한다.

```bat
REM 1) 프롬프트 파일 생성
python tools\llm_bridge.py export -c config.internal.yaml

REM 2) data\output\llm\prompts\*.txt 를 LLM 에 넣고
REM    응답 JSON 을 data\output\llm\answers\ 에 같은 이름(.json)으로 저장

REM 3) 응답을 파이프라인에 반영
python tools\llm_bridge.py apply -c config.internal.yaml
```

Enum 강제, 증거 context id 검증, 근거 부족 시 HIGH 하향 같은 방어 로직이
그대로 적용된다.

---

## 8. 자주 나는 문제

| 증상 | 원인 | 조치 |
|---|---|---|
| `UnicodeEncodeError` | Windows 콘솔이 cp949 | `run_internal.bat` 을 쓰거나 `set PYTHONUTF8=1` |
| `KomoranUnavailableError` | JDK 없음 / JAVA_HOME 미설정 | JDK 설치 후 `setx JAVA_HOME` |
| `SSLError` | 사내 사설 CA | `confluence.verify_ssl` 에 CA 번들 경로 지정 |
| Confluence 404 | v2 경로로 요청 | `api_version: v1` 확인 |
| 페이지 0건 | space key 오타 / 권한 없음 | `--connect` 점검, space key 대문자 확인 |
| LLM 응답 파싱 실패 | 응답 경로가 다름 | `response_path` 를 실제 응답 구조에 맞게 수정 |
| `pip install` 실패 | wheel 이 다른 OS/버전용 | `wheels/` 는 3.9~3.13 win_amd64 를 모두 담고 있다. 그래도 실패하면 `python --version` 과 `platform.machine()` 을 확인한다 |
| `numpy` 버전 충돌 | gensim 이 numpy<2 를 요구 | gensim 을 쓰려면 `wheels-optional\gensim` 으로 설치한다. 안 쓰면 무시해도 된다 |

---

## 9. 반입 전 최종 점검표

- [ ] 내부망 파이썬 버전을 확인하고 그 버전으로 wheel 을 받았다
- [ ] `--platform win_amd64` 로 받았다
- [ ] JDK 반입 여부를 확인했다
- [ ] `data/userdic.txt` 를 포함했다
- [ ] `.env` 나 토큰이 들어간 파일을 **빼고** 압축했다
- [ ] `config.internal.yaml` 의 비밀값이 환경변수 참조(`${...}`)로 되어 있다
