"""코퍼스 문서 레지스트리.

corpus/docs/batch_*.py 의 DOCUMENTS 리스트를 모두 모아 하나의 코퍼스로 제공한다.
Ground Truth 는 이 메타데이터에서 파생되며 Confluence 본문에는 포함되지 않는다.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, List

CATEGORY_TARGETS: Dict[str, int] = {
    "개발자 회의록": 45,
    "API 명세서": 40,
    "기획자와의 회의록": 35,
    "매뉴얼 / 정의서": 25,
    "프로젝트 진행상황": 30,
    "이슈 정리": 35,
    "팀 Ground Rule": 10,
    "코드리뷰": 30,
    "DB 테이블 설계안": 25,
    "비즈니스 기능 설계": 25,
}
TOTAL_TARGET = 300

DEPARTMENTS = {
    "백엔드개발팀", "프론트엔드개발팀", "모바일개발팀", "플랫폼개발팀", "DevOps팀",
    "정보보안팀", "QA팀", "서비스기획팀", "프로젝트관리팀", "데이터팀", "고객지원팀",
}

PROJECTS = {
    "Project Aurora", "Project Falcon", "Project Nova",
    "Project Atlas", "Project Echo",
}

SYSTEMS = {"NEXUS", "ORBIT", "MARS", "AIMS", "BlueGate"}

PEOPLE = {
    "김도윤": "백엔드 개발 / Tech Lead",
    "이서연": "백엔드 개발",
    "박준호": "백엔드 개발",
    "노태윤": "백엔드 개발",
    "최유진": "프론트엔드 개발",
    "정하늘": "프론트엔드 개발",
    "한지민": "모바일 개발",
    "오세훈": "DevOps",
    "윤수빈": "QA",
    "서지호": "QA",
    "강민재": "서비스 기획",
    "송지은": "서비스 기획",
    "임채원": "프로젝트 매니저",
    "배현우": "데이터 엔지니어",
    "조아름": "정보보안",
    "문가영": "고객지원",
}

# 프로젝트별 부모 페이지 (프로젝트가 없는 문서는 공통 문서로 간다)
COMMON_PARENT = "NexBridge 공통 문서"
PARENT_PAGES = [
    "Project Aurora", "Project Falcon", "Project Nova",
    "Project Atlas", "Project Echo", COMMON_PARENT,
]


def load_documents() -> List[Dict[str, Any]]:
    from corpus import docs as docs_package

    documents: List[Dict[str, Any]] = []
    for module_info in sorted(
        pkgutil.iter_modules(docs_package.__path__), key=lambda m: m.name
    ):
        if not module_info.name.startswith("batch_"):
            continue
        module = importlib.import_module(f"corpus.docs.{module_info.name}")
        for document in getattr(module, "DOCUMENTS", []):
            document.setdefault("source_module", module_info.name)
            documents.append(document)
    documents.sort(key=lambda d: d["id"])
    return documents


def parent_for(document: Dict[str, Any]) -> str:
    project = document.get("project")
    return project if project in PROJECTS else COMMON_PARENT


def ground_truth(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "documentId": document["id"],
        "title": document["title"],
        "department": document["department"],
        "documentType": document["type"],
        "project": document.get("project"),
        "entities": list(document.get("systems", [])),
        "features": list(document.get("features", [])),
        "participants": list(document.get("participants", [])),
        "concepts": list(document.get("concepts", [])),
        "dates": [document["date"]] if document.get("date") else [],
        "isDeprecated": bool(document.get("deprecated", False)),
    }


# ----------------------------------------------------------------------
# 유의어 정답 (tools/inject_synonyms.py 가 주입한 표기 변이)
#
# 처음 만든 코퍼스는 한 개념을 한 가지 표기로만 썼기 때문에 유의어 사전
# 파이프라인을 평가할 수 없었다. 실제 조직에서 표기가 갈리는 방식대로
# 변이를 넣고, 그 정답을 여기 명시한다.
#
# relation 값은 app.models.enums.RelationType 과 같은 어휘를 쓴다.
SYNONYM_GROUND_TRUTH: List[Dict[str, Any]] = [
    {"termA": "포인트", "termB": "적립금", "relation": "EXACT_ALIAS",
     "axis": "시기", "note": "2026년 리워드 통합 이전 문서는 '적립금'으로 부른다."},
    {"termA": "회원", "termB": "고객", "relation": "SYNONYM",
     "axis": "부서", "note": "서비스기획팀·고객지원팀 문서는 '고객'으로 쓴다."},
    {"termA": "멱등키", "termB": "중복 방지 키", "relation": "EXACT_ALIAS",
     "axis": "문서 종류", "note": "회의록·운영 문서에서 풀어 쓴 표현."},
    {"termA": "대사", "termB": "정합성 대조", "relation": "EXACT_ALIAS",
     "axis": "문서 종류", "note": "진행상황·설계 문서에서 풀어 쓴 표현."},
    {"termA": "장바구니", "termB": "카트", "relation": "EXACT_ALIAS",
     "axis": "문서 종류", "note": "기획·운영 문서는 화면 용어인 '카트'를 쓴다."},
    {"termA": "배송지", "termB": "수령지", "relation": "EXACT_ALIAS",
     "axis": "문서 종류", "note": "기획·운영 문서는 '수령지'를 쓴다."},
    {"termA": "감사 로그", "termB": "감사 이력", "relation": "EXACT_ALIAS",
     "axis": "문서 종류", "note": "회의록·진행상황 문서는 '감사 이력'으로 쓴다."},
    {"termA": "탈퇴", "termB": "해지", "relation": "NEAR_SYNONYM",
     "axis": "문서 종류", "note": "기획·매뉴얼 문서는 '해지'로 쓴다. 정기결제 해지와 겹칠 여지가 있다."},
]
