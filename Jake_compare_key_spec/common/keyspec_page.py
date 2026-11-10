# -*- coding: utf-8 -*-
"""Landing / PF Popup 이 공통으로 쓰는 Compare 컴포넌트(.pdd43-master-compare) 읽기 + 페이지 안전 호출.

JS 는 기존 Key spec 코드(Compare-main/compare/run_c_landing_live.py, run_popup_ui_live.py)의
JS_STATE / JS_COLUMN / JS_POP_COLUMN 판정 방식을 그대로 쓴다.
  - Key Specs 섹션 노출: .pdd43-master-compare__feature 가 화면에 보이는지
  - 제품(열)별 Key Specs 값: feature-item--N 의 값이 비어 있지 않고 '-' 가 아니면 '값 있음'
  - 텍스트 정규화: 기존 코드와 같은 공백 압축(replace(/\\s+/g, ' ').trim()) 만 사용

2026-11-10 보완
  - 루트 선택: document.querySelector 의 '첫 번째' 루트가 아니라 '화면에 보이는' 루트를 읽는다.
    (CA PF: 팝업이 떠 있는데 숨은 다른 pdd43 루트를 읽어 '팝업 SKU []' 로 오판한 6행)
  - ev()/wf(): 페이지가 스스로 다시 이동(리다이렉트·새로고침)해 'Execution context was destroyed' 가 나면
    로드를 기다렸다가 다시 실행한다 (CA / CA_FR Landing 30행).
"""
import asyncio
import logging
import re
from pathlib import Path

from common.config import DIAG_SCREENSHOTS, RESULT_FAIL, RESULT_PASS, TIMEOUTS

# 화면에 보이는 루트 우선, 없으면 첫 번째 (기존과 같음). rootSel 은 CSS 선택자
_JS_PICK_ROOT = r"""
  const vis = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
                      && e.getBoundingClientRect().height > 0);
  const roots = [...document.querySelectorAll(rootSel)];
  const root = roots.find(vis) || roots[0] || null;
"""

# 기존 run_c_landing_live.JS_STATE 와 같은 판정 (cols: 열 번호 → 값 있는/없는 항목 수)
JS_STATE = r"""(rootSel) => {""" + _JS_PICK_ROOT + r"""
  if (!root) return {hasRoot: false};
  const feat = root.querySelector('.pdd43-master-compare__feature');
  const head = feat && feat.querySelector('.pdd43-master-compare__sub-headline');
  const lists = feat ? [...feat.querySelectorAll('ul.pdd43-master-compare__feature-list')] : [];
  const cols = {};
  for (const ul of lists) {
    for (const li of ul.querySelectorAll('li.pdd43-master-compare__feature-item')) {
      const m = li.className.match(/feature-item--(\d+)/); const c = m ? m[1] : '?';
      const v = (li.querySelector('.pdd43-master-compare__feature-value') || {}).innerText || '';
      cols[c] = cols[c] || {filled: 0, empty: 0};
      if (v.trim() && v.trim() !== '-') cols[c].filled++; else cols[c].empty++;
    }
  }
  return {hasRoot: true, roots: roots.length, title: head ? head.innerText.trim() : '',
          featVisible: !!(feat && feat.offsetParent !== null && feat.getBoundingClientRect().height > 0),
          rows: lists.length, cols};
}"""

# 열 N 의 제품명 / SKU (기존 JS_POP_COLUMN · JS_COLUMN 의 name / sku 와 같은 선택자). 화면에 보이는 루트 기준.
# innerText 가 비면(접힌 영역) textContent 로 한 번 더 읽는다.
JS_COLUMNS = r"""(rootSel) => {""" + _JS_PICK_ROOT + r"""
  if (!root) return [];
  const out = [];
  for (const card of root.querySelectorAll('li.pdd43-master-compare__card-item')) {
    const m = card.className.match(/__column--(\d+)/);
    const txt = s => { const e = card.querySelector(s); if (!e) return '';
                       return ((e.innerText || '').trim() ? e.innerText : (e.textContent || '')).replace(/\s+/g, ' ').trim(); };
    out.push({col: m ? +m[1] : null,
              name: txt('.pdd43-master-compare__name-text') || txt('.menu__select-field-text'),
              sku: txt('.pdd43-master-compare__sku-text')});
  }
  return out;
}"""

# 기존 dismiss_popups 와 같은 쿠키 배너 선택자 (거부/필수만 허용)
COOKIE_SELECTORS = ("#truste-consent-required", "#onetrust-reject-all-handler", "button[id*='reject' i]")

_NAV_ERRORS = ("Execution context was destroyed", "navigation", "Cannot find context", "frame was detached")


def is_nav_error(error) -> bool:
    return any(s in str(error) for s in _NAV_ERRORS)


async def _settle(page):
    try:
        await page.wait_for_load_state("load", timeout=TIMEOUTS["page_load"])
    except Exception as error:   # 이미 닫힌 페이지 등 → 호출 측에서 다시 판단
        logging.debug("[PAGE] load 대기 실패: %s", type(error).__name__)


async def ev(page, js, arg=None, attempts=3):
    """page.evaluate + 페이지 재이동으로 실행 컨텍스트가 사라지면 로드 후 다시 실행."""
    for i in range(attempts):
        try:
            return await (page.evaluate(js) if arg is None else page.evaluate(js, arg))
        except Exception as error:
            if not is_nav_error(error) or i == attempts - 1:
                raise
            logging.info("[PAGE] 실행 중 페이지 이동 감지 → 로드 후 재실행 (%s)", page.url)
            await _settle(page)


async def wf(page, js, arg=None, timeout=None, attempts=3):
    """page.wait_for_function + 페이지 재이동 대응 (시간 초과는 그대로 올림)."""
    for i in range(attempts):
        try:
            return await page.wait_for_function(js, arg=arg, timeout=timeout)
        except Exception as error:
            if not is_nav_error(error) or i == attempts - 1:
                raise
            await _settle(page)


async def dismiss_cookie_banner(page, wait_appear_ms=0):
    """쿠키 배너를 거부(필수만)로 닫는다.

    wait_appear_ms=0 : 지금 보이는 배너만 닫음 (기다리지 않음)
    wait_appear_ms>0 : 배너가 늦게 뜨는 경우(TrustArc) 그 시간 안에 보이면 닫음 - 클릭이 가로채였을 때만 사용
    """
    if wait_appear_ms:
        try:
            await page.locator(", ".join(COOKIE_SELECTORS)).first.wait_for(state="visible", timeout=wait_appear_ms)
        except Exception as error:
            logging.debug("[COOKIE] 배너가 보이지 않음: %s", type(error).__name__)
            return
    for sel in COOKIE_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=5000)
                await loc.wait_for(state="hidden", timeout=5000)
                return
        except Exception as error:   # 배너가 사라지며 클릭이 빗나가는 경우 등 - 검증과 무관
            logging.debug("[COOKIE] %s 닫기 실패: %s", sel, type(error).__name__)


def timeout_note(error) -> str:
    """Playwright 시간 초과 메시지 요약: 첫 줄 + 원인 줄(가림 요소 / 불안정 / 분리)."""
    lines = str(error).splitlines()
    cause = next((l.strip(" -") for l in lines if "intercepts pointer" in l or "not stable" in l
                  or "detached" in l), "")
    return f"대기 시간 초과: {lines[0] if lines else type(error).__name__}" + (f" ({cause[:150]})" if cause else "")


def norm(text) -> str:
    """기존 코드와 같은 공백 압축만 적용."""
    return " ".join(str(text or "").split())


async def read_state(page, root_sel=".pdd43-master-compare"):
    state = await ev(page, JS_STATE, root_sel)
    columns = await ev(page, JS_COLUMNS, root_sel) if state.get("hasRoot") else []
    return state, columns


# ------------------------------------------------------------------ 판정 (기존 규칙 그대로)
V_PASS, V_EMPTY, V_HIDDEN = "PASS", "EMPTY", "HIDDEN"


def column_verdict(state, col):
    """열 col 의 Key Specs 상태: (V_PASS | V_EMPTY | V_HIDDEN, 설명).
    HIDDEN = 섹션 자체가 안 보임 (세트 중 Key Specs 없는 제품이 있으면 섹션 전체가 숨는 것이 규칙 → 원인 제품 미확정)
    EMPTY  = 섹션은 보이는데 이 열 값이 없음 (원인 제품 = 이 열)"""
    if not state.get("featVisible") or not state.get("cols"):
        return V_HIDDEN, "Key Specs 섹션 미노출"
    c = state["cols"].get(str(col))
    if not c:
        return V_EMPTY, f"Key Specs 에 {col}열 값이 없음"
    detail = f"값 {c['filled']}/{c['filled'] + c['empty']}"
    return (V_PASS if c["filled"] > 0 else V_EMPTY), detail


def keyspec_result(state, col, missing_section_result, missing_section_note):
    """열 col 의 Key spec 존재 결과 (기존 Landing/Popup 판정 그대로).

    섹션 미노출 처리: Landing = FAIL, Popup = 확인 필요 (세트 중 Key Specs 없는 제품이 있으면 섹션 전체가 숨는 게 규칙)
    """
    verdict, detail = column_verdict(state, col)
    if verdict == V_HIDDEN:
        return missing_section_result, missing_section_note
    return (RESULT_PASS if verdict == V_PASS else RESULT_FAIL), detail


# ------------------------------------------------------------------ 진단 스크린샷
DIAG_DIR = None          # runner 가 실행마다 Result/logs/<로그이름>_diag 로 지정
_diag_count = 0


def set_diag_dir(path):
    global DIAG_DIR, _diag_count
    DIAG_DIR, _diag_count = (Path(path) if path else None), 0


async def snap(page, name) -> str:
    """실패 진단용 화면 캡처 (실행당 DIAG_SCREENSHOTS 장까지). 반환: 파일 이름 또는 ''."""
    global _diag_count
    if DIAG_DIR is None or _diag_count >= DIAG_SCREENSHOTS:
        return ""
    _diag_count += 1
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", name)[:80]
    path = DIAG_DIR / f"{_diag_count:03d}_{safe}.png"
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.wait_for(page.screenshot(path=str(path), full_page=False), timeout=15)
        return path.name
    except Exception as error:
        logging.debug("[DIAG] 스크린샷 실패: %s", type(error).__name__)
        return ""
