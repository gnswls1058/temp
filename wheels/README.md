# 오프라인 설치용 wheel

Windows 64bit(win_amd64), Python **3.9 / 3.10 / 3.11 / 3.12 / 3.13** 용을 모두 담았다.
파일 이름의 `cp39`, `cp311` 이 파이썬 버전이며, pip 이 자동으로 맞는 것을 고른다.

## 설치

```bat
python -m pip install --no-index --find-links wheels -r requirements-internal.txt
```

인터넷을 보지 않으므로(`--no-index`) 이 폴더 안에서만 해결한다.

## 담긴 것

| 패키지 | 역할 |
|---|---|
| PyYAML | 설정 파일 |
| requests (+urllib3, certifi, charset-normalizer, idna) | Confluence / 사내 LLM 통신 |
| konlpy | 형태소 분석 (Komoran jar 포함) |
| JPype1 | konlpy 가 JVM 을 띄우는 다리 |
| lxml, numpy | konlpy 가 요구하는 필수 의존성 |

**JDK 는 여기 없다.** Komoran 이 JVM 위에서 돌기 때문에 JDK 8 이상을 따로 설치해야 한다.

## 선택 패키지

`wheels-optional/` 에 있다.

```bat
REM 자체 테스트용
python -m pip install --no-index --find-links wheels-optional pytest python-dotenv

REM FastText 를 쓰려면 (Python 3.10~3.12 만 가능)
python -m pip install --no-index --find-links wheels-optional\gensim gensim
```

gensim 4.3.3 은 numpy<2 를 요구해서 필수 세트의 numpy 2.x 와 충돌한다.
그래서 별도 폴더에 numpy 1.26 과 함께 담았다. 설치하면 numpy 가 1.26 으로 내려간다.
