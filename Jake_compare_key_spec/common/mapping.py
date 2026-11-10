# -*- coding: utf-8 -*-
"""Docs/All key spec Check.xlsx 로딩 + 사업부별 Product Set 선정/검증.

시트 = 사업부(MX / VD / DA), 컬럼은 세 시트 모두
  SITE_CD, IA_CD, CATEGORY_NAME, FMY_ID, FMY_ENG_NM, FMY_MKT_NM, REP_MODEL, DISPLAY_STATUS
최신/중간 제품을 나타내는 컬럼은 없으므로 PRODUCT_SET_RULE 이 정해지기 전에는
제품을 고르지 않고 '확인 필요' 로 돌려준다 (임의·랜덤 선정 금지).
"""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from common.config import (DIVISIONS, MAPPING_COLUMNS, PAGE_IA_PREFIX, RESULT_CHECK, SET_CATEGORIES,
                           SET_SIZE)

SET_KINDS = (("Set 1", "Latest"), ("Set 2", "Middle"))
_BLANK_RUN_STOP = 50   # VD/DA 시트는 서식만 있는 빈 행이 100만 줄 이상 → 연속 빈 행에서 멈춤


class MappingError(Exception):
    """매핑 파일이 없거나 시트/컬럼 구조가 다름."""


@dataclass(frozen=True)
class Product:
    division: str
    site: str
    row: int
    ia: str
    category: str
    fmy_id: str
    fmy_eng: str
    fmy_mkt: str
    rep_model: str


@dataclass
class ProductSet:
    division: str
    set_no: str
    kind: str
    site: str
    products: list = field(default_factory=list)
    status: str = ""        # "OK" 또는 RESULT_CHECK
    reason: str = ""
    page_key: str = ""      # 이 Set 을 검증할 페이지(CATEGORY_SPECS 키). 비어 있으면 사업부 페이지를 순서대로 탐색
    alternates: list = field(default_factory=list)   # 화면에 없는 제품을 바꿀 대체 후보 (우선순위 순)

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def load_mapping(path, sites):
    """sites 에 해당하는 DISPLAY_STATUS=Y 제품 → ({사업부: {site: [Product]}}, 데이터 이슈 목록)."""
    path = Path(path)
    if not path.exists():
        raise MappingError(f"매핑 파일이 없습니다: {path}")
    want = {s.lower() for s in sites}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        missing_sheets = [d for d in DIVISIONS if d not in wb.sheetnames]
        if missing_sheets:
            raise MappingError(f"매핑 파일에 사업부 시트가 없습니다: {missing_sheets} (시트: {wb.sheetnames})")
        data, issues = {}, []
        for div in DIVISIONS:
            rows = wb[div].iter_rows(values_only=True)
            header = [_s(h) for h in (next(rows, None) or [])]
            lacking = [c for c in MAPPING_COLUMNS if c not in header]
            if lacking:
                raise MappingError(f"'{div}' 시트에 컬럼이 없습니다: {lacking}")
            idx = {c: header.index(c) for c in MAPPING_COLUMNS}
            by_site, blank_run = {}, 0
            for n, r in enumerate(rows, start=2):
                if not any(_s(v) for v in r):
                    blank_run += 1
                    if blank_run > _BLANK_RUN_STOP:
                        break
                    continue
                blank_run = 0
                site = _s(r[idx["SITE_CD"]]).lower()
                if site not in want or _s(r[idx["DISPLAY_STATUS"]]).upper() != "Y":
                    continue
                p = Product(div, site, n, _s(r[idx["IA_CD"]]), _s(r[idx["CATEGORY_NAME"]]),
                            _s(r[idx["FMY_ID"]]), _s(r[idx["FMY_ENG_NM"]]), _s(r[idx["FMY_MKT_NM"]]),
                            _s(r[idx["REP_MODEL"]]))
                if not p.rep_model:
                    issues.append(f"[{div}] {site} {n}행: REP_MODEL 공백 ({p.fmy_mkt})")
                by_site.setdefault(site, []).append(p)
            for site, items in by_site.items():
                dup = [m for m, c in Counter(p.rep_model for p in items if p.rep_model).items() if c > 1]
                for m in dup:
                    issues.append(f"[{div}] {site}: REP_MODEL 중복 {m}")
            data[div] = by_site
        return data, issues
    finally:
        wb.close()


def validate_set(ps: ProductSet) -> ProductSet:
    """Set 무결성: 3개 / REP_MODEL 공백 없음 / REP_MODEL Unique / 같은 PF 묶음(IA 앞 4자리, 기존 Key spec 규칙)."""
    problems = []
    if len(ps.products) != SET_SIZE:
        problems.append(f"제품 수 {len(ps.products)}개 (필요 {SET_SIZE}개)")
    blanks = [p.row for p in ps.products if not p.rep_model]
    if blanks:
        problems.append(f"REP_MODEL 공백 (행 {blanks})")
    models = [p.rep_model for p in ps.products if p.rep_model]
    if len(set(models)) != len(models):
        problems.append(f"REP_MODEL 중복 {[m for m, c in Counter(models).items() if c > 1]}")
    if len({p.ia[:4] for p in ps.products}) > 1:
        problems.append("IA_CD 앞 4자리가 달라 같은 PF 에서 비교할 수 없음")
    if len({p.division for p in ps.products}) > 1:
        problems.append("사업부가 섞여 있음")
    if problems:
        ps.status, ps.reason = RESULT_CHECK, "; ".join(problems)
    else:
        ps.status, ps.reason = "OK", ""
    return ps


def _fmy_id_key(p):
    return (int(p.fmy_id) if p.fmy_id.isdigit() else -1, p.row)


def rule_fmy_id(items, division=None):
    """FMY_ID 순서 규칙 (사용자 지정, 2026-11-09).

    1) 범위: 사업부 안에서 FMY_ID 가 가장 큰 제품이 속한 PF 묶음(IA_CD 앞 4자리, 기존 Key spec 규칙)
       → 한 Set 의 3개 제품이 같은 PF 에서 비교되도록 하기 위함
    2) 그 묶음을 FMY_ID 오름차순 정렬
       Latest = FMY_ID 가 가장 큰 3개
       Middle = Latest 를 뺀 나머지의 가운데 3개 (나머지 n 개 중 인덱스 n//2-1 ~ n//2+1)
    REP_MODEL 공백 제품은 정렬 대상에서 뺀다. 개수가 모자라면 validate_set 에서 '확인 필요'.
    """
    usable = [p for p in items if p.rep_model and p.fmy_id.isdigit()]
    if not usable:
        return {}
    newest = max(usable, key=_fmy_id_key)
    group = sorted((p for p in usable if p.ia[:4] == newest.ia[:4]), key=_fmy_id_key)
    latest = group[-SET_SIZE:]
    rest = group[:-SET_SIZE] if len(group) > SET_SIZE else []
    m = len(rest) // 2
    middle = rest[max(0, m - 1): max(0, m - 1) + SET_SIZE]
    return {"Latest": latest, "Middle": middle}


def _latest_middle(group):
    """FMY_ID 오름차순 group → Latest = 상위 3개, Middle = Latest 를 뺀 나머지의 가운데 3개."""
    latest = group[-SET_SIZE:]
    rest = group[:-SET_SIZE] if len(group) > SET_SIZE else []
    m = len(rest) // 2
    middle = rest[max(0, m - 1): max(0, m - 1) + SET_SIZE]
    return latest, middle


def _by_distance(pool, center):
    """pool(FMY_ID 오름차순)을 center 인덱스에 가까운 순서로 (같은 거리면 FMY_ID 큰 쪽 먼저)."""
    return [p for _, p in sorted(enumerate(pool), key=lambda t: (abs(t[0] - center), -t[0]))]


def rule_category_fmy_id(items, division):
    """카테고리 + FMY_ID 규칙 (사용자 지정, 2026-11-09 피드백). 모든 국가 / Landing·Popup 공통.

      MX: Latest = Smartphones, Middle = Tablets
      VD: Latest = TVs,         Middle = TVs
      DA: Latest = Refrigerators, Middle = Washers and Dryers(Laundry)
    카테고리 = IA_CD 앞 4자리 (config.PAGE_IA_PREFIX). 카테고리 안을 FMY_ID 오름차순 정렬해
      Latest = FMY_ID 상위 3개
      Middle = (Latest 를 뺀) 나머지의 가운데 3개
    Latest 와 Middle 카테고리가 다르면 Middle 은 그 카테고리 전체의 가운데 3개.
    대체 후보 (화면에 없는 제품을 바꿀 때 쓰는 순서):
      Latest = 그 다음으로 FMY_ID 가 큰 제품부터
      Middle = 가운데에 가까운 제품부터
    반환: {"Latest": (page_key, [Product], [대체 후보]), "Middle": (...)}
    """
    out = {}
    usable = [p for p in items if p.rep_model and p.fmy_id.isdigit()]
    plan = SET_CATEGORIES.get(division, {})
    for kind in ("Latest", "Middle"):
        key = plan.get(kind)
        if not key:
            continue
        prefix = PAGE_IA_PREFIX[key]
        group = sorted((p for p in usable if p.ia.startswith(prefix)), key=_fmy_id_key)
        if kind == "Middle" and plan.get("Latest") != key:
            m = len(group) // 2                       # 카테고리 전체의 가운데 3개
            start = max(0, min(m - 1, len(group) - SET_SIZE))
            picked = group[start:start + SET_SIZE]
            alternates = _by_distance([p for p in group if p not in picked], start)
        else:
            latest, middle = _latest_middle(group)
            if kind == "Latest":
                picked = latest
                alternates = list(reversed(group[:-SET_SIZE])) if len(group) > SET_SIZE else []
            else:
                picked = middle
                rest = group[:-SET_SIZE] if len(group) > SET_SIZE else []
                alternates = _by_distance([p for p in rest if p not in picked], max(0, len(rest) // 2 - 1))
        out[kind] = (key, picked, alternates)
    return out


RULES = {"fmy_id": rule_fmy_id, "category_fmy_id": rule_category_fmy_id}


def select_product_sets(mapping, site, rule):
    """사업부마다 Set 1(Latest) / Set 2(Middle) 을 돌려준다.

    rule: callable(list[Product], division) -> {"Latest": [Product x3] 또는 (page_key, [Product x3]), "Middle": ...}
          (같은 입력이면 항상 같은 결과를 내는 결정적 규칙이어야 함)
    rule 이 None 이면 기준이 없으므로 모든 Set 을 '확인 필요' 로 돌려준다.
    """
    if isinstance(rule, str):
        rule = RULES[rule]
    sets = []
    for div in mapping:
        items = mapping[div].get(site, [])
        picked = None
        if rule is not None and items:
            picked = rule(list(items), div)
        for set_no, kind in SET_KINDS:
            ps = ProductSet(div, set_no, kind, site)
            if not items:
                ps.status, ps.reason = RESULT_CHECK, f"매핑 파일 '{div}' 시트에 {site} 제품이 없음"
            elif rule is None:
                ps.status = RESULT_CHECK
                ps.reason = ("최신/중간 제품 판별 기준이 매핑 파일에 없음 "
                             f"(컬럼: {', '.join(MAPPING_COLUMNS)}) → PRODUCT_SET_RULE 확인 필요")
            else:
                value = (picked or {}).get(kind) or []
                if isinstance(value, tuple):          # (page_key, products[, alternates])
                    ps.page_key, value, *alt = value
                    ps.alternates = list(alt[0]) if alt else []
                ps.products = list(value)
                validate_set(ps)
                if not ps.ok and ps.page_key:
                    ps.reason = f"[{ps.page_key}] {ps.reason}"
            sets.append(ps)
    # 같은 사업부의 다른 Set 에 이미 선정된 제품은 대체 후보에서 뺀다 (VD 는 Latest / Middle 모두 TV)
    for ps in sets:
        taken = {p.rep_model for o in sets if o.division == ps.division and o is not ps for p in o.products}
        ps.alternates = [p for p in ps.alternates if p.rep_model not in taken]
    return sets


def substitution_plan(ps, available, used=()):
    """화면에 있는 제품만으로 Set 을 다시 구성한다.

    available(product) -> bool : 화면에서 그 제품을 찾을 수 있는지 (비동기 아님, 호출 측에서 미리 계산)
    used : 같은 사업부의 앞 Set 에서 이미 쓴 REP_MODEL (중복 방지)
    반환: (최종 제품 목록, {대체 제품 REP_MODEL: 원래 제품})  - 원래 제품 순서를 유지하고 빠진 자리에 후보를 채움
    """
    from common.config import MAX_ALTERNATES
    final, replaced = [], {}
    pool = [p for p in ps.alternates if p.rep_model not in used][:MAX_ALTERNATES]
    for orig in ps.products:
        if available(orig):
            final.append(orig)
            continue
        while pool:
            cand = pool.pop(0)
            if available(cand):
                final.append(cand)
                replaced[cand.rep_model] = orig
                break
    return final, replaced
