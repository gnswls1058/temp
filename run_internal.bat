@echo off
chcp 65001 > nul
REM ============================================================
REM  내부망 실행 스크립트 (Windows)
REM  한글 로그가 깨지지 않도록 콘솔 코드페이지를 UTF-8 로 바꾼다.
REM ============================================================
setlocal

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM --- JDK 경로 (Komoran 실행에 필요) ---
REM 설치 경로에 맞게 수정한다.
if "%JAVA_HOME%"=="" set JAVA_HOME=C:\Program Files\Java\jdk-17

REM --- 비밀값 (또는 시스템 환경변수로 등록) ---
REM set CONFLUENCE_BASE_URL=https://confluence.company.co.kr
REM set CONFLUENCE_API_TOKEN=...
REM set INTERNAL_LLM_URL=http://llm.company.co.kr/v1/chat/completions
REM set INTERNAL_LLM_MODEL=internal-model

if not exist .venv (
    echo [1/3] 가상환경 생성
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo [2/3] 패키지 설치 (오프라인)
    python -m pip install --no-index --find-links wheels --upgrade pip
    python -m pip install --no-index --find-links wheels -r requirements-internal.txt
) else (
    call .venv\Scripts\activate.bat
)

echo [3/3] 환경 점검
python tools\check_environment.py -c config.internal.yaml
if errorlevel 1 (
    echo.
    echo 환경 점검에서 실패 항목이 나왔다. 위 내용을 먼저 해결한다.
    exit /b 1
)

echo.
echo === 파이프라인 실행 ===
python -m app.main build -c config.internal.yaml %*

endlocal
