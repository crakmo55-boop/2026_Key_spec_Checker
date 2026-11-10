# -*- coding: utf-8 -*-
"""두 Checker 공통 실행 흐름.

  base_urls.xlsx → 국가/환경 → 매핑 로딩 → Product Set 선정
  → (실행 가능한 Set 이 있는 국가만) 브라우저 + 로그인(p6-pre-qa3)/생략(Live)
  → MX / VD / DA 병렬 (같은 국가 Context 안에서 사업부마다 Page 1개)
  → 결과 행 수집 → ResultWriter 하나로 저장

국가는 순서대로 하나씩 처리한다 (국가·환경마다 별도 브라우저/Context → 세션 공유 없음).
Product Set 이 '확인 필요' 면 브라우저를 열지 않고 결과에 '확인 필요' 로만 기록한다.
"""
import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from common.config import (COUNTRY_PARALLEL, DEFAULT_RETRIES, DIVISIONS, HEADED, MAPPING_FILE, PRODUCT_SET_RULE,
                           RESULT_CHECK, RESULT_DIR, URL_FILE)
from common.keyspec_page import set_diag_dir
from common.mapping import MappingError, load_mapping, select_product_sets
from common.result import ResultWriter, set_rows
from common.session import SessionError, SessionPool, open_session, setup_logging
from common.targets import TargetError, load_targets


def parse_args(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                    help=f"국가 처리 실패 시 추가 재시도 횟수 (기본 {DEFAULT_RETRIES})")
    ap.add_argument("--parallel", type=int, default=COUNTRY_PARALLEL,
                    help=f"동시에 처리할 국가 수 (기본 {COUNTRY_PARALLEL}, 1 이면 순서대로)")
    ap.add_argument("--dry-run", action="store_true",
                    help="국가/환경/로그인 여부/Product Set 만 확인하고 종료 (브라우저·로그인 없음)")
    return ap.parse_args()


def bootstrap():
    """공통 준비. 반환: [(target, [ProductSet x6])]. 실행 불가 입력이면 None."""
    try:
        targets = load_targets(URL_FILE)
    except TargetError as error:
        logging.error("%s", error)
        return None
    try:
        mapping, issues = load_mapping(MAPPING_FILE, [t.site for t in targets])
    except MappingError as error:
        logging.error("%s", error)
        return None
    for issue in issues:
        logging.warning("[MAPPING] %s", issue)

    plan = []
    for t in targets:
        sets = select_product_sets(mapping, t.site, PRODUCT_SET_RULE)
        plan.append((t, sets))
        logging.info("[TARGET] %s | %s | 국가 %s | 로그인 %s", t.url, t.env_label, t.site,
                     "수행" if t.needs_login else "생략")
        for ps in sets:
            models = ", ".join(p.rep_model for p in ps.products) or "-"
            logging.info("    %s %s %-6s [%s] %s | %s %s", ps.division, ps.set_no, ps.kind, ps.page_key or "-",
                         models, "OK" if ps.ok else ps.status, ps.reason)
    return plan


async def _run_target(pool, target, sets, page_type, check_division):
    """국가 1건: 공유 로그인 세션 받기 → 사업부 병렬 → 행 목록. 실패하면 예외."""
    runnable = {div: [ps for ps in sets if ps.division == div and ps.ok] for div in DIVISIONS}
    runnable = {div: v for div, v in runnable.items() if v}
    session = await pool.session_for(target)
    results = await asyncio.gather(
        *(check_division(session, target, div, div_sets) for div, div_sets in runnable.items()),
        return_exceptions=True,
    )
    rows, errors = [], []
    for (div, div_sets), res in zip(runnable.items(), results):
        if isinstance(res, Exception):
            errors.append(f"{div}: {type(res).__name__}: {res}")
            for ps in div_sets:
                rows += set_rows(target, page_type, ps, RESULT_CHECK, f"검증 중 오류: {type(res).__name__}: {res}")
        else:
            rows += res
    return rows, errors


async def _run_country(pool, target, sets, page_type, check_division, retries):
    """국가 1건 + 재시도. 반환: 이 국가의 결과 행 (예외를 밖으로 내보내지 않음)."""
    blocked = [ps for ps in sets if not ps.ok]
    target_rows = [r for ps in blocked for r in set_rows(target, page_type, ps, RESULT_CHECK, ps.reason)]
    if len(blocked) == len(sets):
        logging.warning("[SKIP] %s - 실행 가능한 Product Set 없음 (브라우저/로그인 생략)", target.url)
        return _ordered(target_rows)
    last_rows, attempts = [], 1 + max(0, retries)
    for attempt in range(1, attempts + 1):
        logging.info("[START] %s %s (시도 %d/%d)", page_type, target.url, attempt, attempts)
        try:
            last_rows, errors = await _run_target(pool, target, sets, page_type, check_division)
        except Exception as error:   # 로그인/브라우저/세션 실패 → 이 국가만 '확인 필요', 다른 국가 계속
            kind = "로그인/세션 실패" if isinstance(error, SessionError) else f"실행 오류 ({type(error).__name__})"
            detail = str(error).splitlines()[0] if str(error) else ""
            logging.exception("[ERROR] %s %s", target.url, kind)
            errors = [f"{kind}: {detail}"]
            last_rows = [r for ps in sets if ps.ok
                         for r in set_rows(target, page_type, ps, RESULT_CHECK, f"{kind}: {detail}")]
        if not errors:
            break
        logging.warning("[INCOMPLETE] %s - %s", target.url, " | ".join(errors))
    return _ordered(target_rows + last_rows)


async def _run_all(plan, page_type, check_division, writer, retries, headless, parallel=COUNTRY_PARALLEL,
                   pool_factory=None):
    """국가를 parallel 개씩 동시에 처리한다. 로그인 세션은 SessionPool 하나를 모든 국가가 공유.
    국가가 끝날 때마다 그때까지의 전체 결과(base_urls 순서)를 Writer 하나로 다시 저장한다."""
    from playwright.async_api import async_playwright

    done = {}                                  # plan 인덱스 → 행
    sem = asyncio.Semaphore(max(1, parallel))

    def snapshot():
        return [r for i in sorted(done) for r in done[i]]

    async with async_playwright() as playwright:
        pool = pool_factory(playwright) if pool_factory else SessionPool(playwright, headless=headless)
        try:
            async def one(i, target, sets):
                async with sem:
                    t0 = time.monotonic()
                    done[i] = await _run_country(pool, target, sets, page_type, check_division, retries)
                    logging.info("[COUNTRY] %s 완료 %.1f초 (%d/%d)", target.site, time.monotonic() - t0,
                                 len(done), len(plan))
                    writer.save(snapshot())    # 이벤트 루프 단일 스레드 → 동시 저장 없음
            await asyncio.gather(*(one(i, t, s) for i, (t, s) in enumerate(plan)))
        finally:
            logging.info("[SESSION] 로그인 횟수 %d회 (국가 %d개)", getattr(pool, "logins", 0), len(plan))
            await pool.close()
    return snapshot()


def _ordered(rows):
    """사업부(MX→VD→DA) → Set 순서로 정렬 (같은 Set 안의 제품 순서는 유지)."""
    order = {d: i for i, d in enumerate(DIVISIONS)}
    return sorted(rows, key=lambda r: (order.get(r["사업부"], 99), r["Set"]))


def run_checker(page_type, check_division, output_file, description):
    """Entry Point 공통 main. check_division(session, target, division, sets) -> [row]"""
    log_file = RESULT_DIR / "logs" / f"{Path(output_file).stem}_{datetime.now():%Y%m%d_%H%M%S}.log"
    setup_logging(log_file)
    set_diag_dir(log_file.with_name(f"{log_file.stem}_diag"))     # 실패 진단 캡처 (실패가 있을 때만 생성)
    args = parse_args(description)
    plan = bootstrap()
    if plan is None:
        return 2
    if args.dry_run:
        logging.info("[DRY-RUN] 브라우저·로그인 없이 종료")
        return 0
    rows = asyncio.run(_run_all(plan, page_type, check_division, ResultWriter(output_file),
                                args.retries, headless=not HEADED, parallel=args.parallel))
    counts, sku_counts = {}, {}
    for r in rows:
        counts[r["Key spec 존재 결과"]] = counts.get(r["Key spec 존재 결과"], 0) + 1
        sku_counts[r["SKU 매칭 결과"]] = sku_counts.get(r["SKU 매칭 결과"], 0) + 1
    logging.info("[DONE] %s %d행 Key spec %s | SKU 매칭 %s", page_type, len(rows), counts, sku_counts)
    swapped = [r for r in rows if "[Key spec 대체]" in str(r.get("비고", ""))]
    if swapped:   # Key Specs 가 없어 바꾼 원래 제품 = 결함 후보 (비고 앞부분에 원래 제품·사유가 있음)
        logging.info("[DONE] Key spec 대체 %d행 (원래 제품은 결함 후보 - 로그의 [KEYSPEC-대체] 줄 참고)", len(swapped))
        for r in swapped:
            logging.info("    %s %s %s %s | %s", r["국가"], r["사업부"], r["Set"], r["SKU"], str(r["비고"])[:160])
    return 0


def entry(page_type, check_division, output_file, description):
    sys.exit(run_checker(page_type, check_division, output_file, description))
