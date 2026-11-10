# -*- coding: utf-8 -*-
"""브라우저 / 로그인 / 세션 (New_GNB_Code_Update 의 방식을 그대로 옮김).

GNB 에서 가져온 것
  - 로그인: login/WDS_login_FS_Auto.samsung_login_and_move (계정은 login/.env 의 LOGIN_ID_Auto / LOGIN_PW_Auto)
  - goLink: javascript:goLink('p6-preqa3') / cn 은 'p6-preqa3-cn'
  - 세션 만료 판단: AEM 로그인 화면 (URL 패턴 / 제목 / 'Please login through WMC' 문구)  = GNB page_is_aem_login
  - 세션 재획득: WDS goLink 재클릭 → 실패하면 WDS 재로그인 + goLink        = GNB recover_fs_session
  - 세션 저장/범위: BrowserContext 의 쿠키. 국가(URL) 1건마다 로그인 Context 1개 (GNB_Checker 와 같은 범위)
    사업부 워커는 이 세션 쿠키를 복사한 별도 Context 를 쓴다 (같은 국가·환경만, Compare 선택 상태 간섭 방지)

GNB 대비 보완한 것 (분석 보고서에서 지적한 문제)
  - goLink 뒤 sleep(2) + 탭 목록 비교 대신 context.expect_page() 로 새 탭을 직접 받음
  - 같은 Context 의 워커(MX/VD/DA)가 동시에 만료를 만나도 재획득은 한 번만 (lock + 세대 번호)
  - 재로그인 시 다른 워커의 탭을 닫지 않음
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from common.config import QA_HOST_MARK, TIMEOUTS, VIEWPORT

_SECRET_ENV_KEYS = ("LOGIN_ID_Auto", "LOGIN_PW_Auto", "LOGIN_ID", "LOGIN_PW")


class SessionError(Exception):
    """로그인 실패 또는 세션 재획득 실패."""


# ------------------------------------------------------------------ 로그 보호
class RedactSecretsFilter(logging.Filter):
    """로그 메시지에 .env 계정 값이 섞여 나오면 가린다 (안전장치)."""

    def filter(self, record):
        secrets = [v for v in (os.getenv(k) for k in _SECRET_ENV_KEYS) if v and len(v) >= 3]
        if secrets:
            msg = record.getMessage()
            for s in secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, None
        return True


class RedactingFormatter(logging.Formatter):
    """traceback 까지 포함한 최종 문자열에서 .env 계정 값을 가린다."""

    def format(self, record):
        text = super().format(record)
        for s in (v for v in (os.getenv(k) for k in _SECRET_ENV_KEYS) if v and len(v) >= 3):
            text = text.replace(s, "***")
        return text


def setup_logging(log_file=None):
    """콘솔 + (지정 시) 실행 로그 파일. 두 곳 모두 계정 값 마스킹."""
    fmt = RedactingFormatter("%(asctime)s [%(levelname)s] %(message)s")
    handlers = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactSecretsFilter())
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(logging.INFO)
    if log_file:
        logging.info("[LOG] 실행 로그: %s", log_file)


# ------------------------------------------------------------------ GNB 와 같은 세션 판단
def is_aem_login_url(url) -> bool:
    """STG 세션/쿠키 만료로 AEM 로그인 페이지에 떨어진 경우 (GNB_Checker.is_aem_login_url)."""
    if not url:
        return False
    low = url.lower()
    return ("/apps/samsung/login/" in low or ("login.html" in low and "j_reason" in low)
            or "$$login$$" in low or "login/content/login" in low)


async def page_is_aem_login(page) -> bool:
    """AEM/WMC 로그인 화면인지 URL·제목·문구로 확인 (GNB_Checker.page_is_aem_login)."""
    if page.is_closed():
        return False
    if is_aem_login_url(page.url):
        return True
    title = await page.title() or ""
    if "AEM" in title and ("로그인" in title or "login" in title.lower()):
        return True
    return bool(await page.get_by_text("Please login through WMC", exact=False).count())


# WMC 공지 레이어: div[name="wmcNotice"] (class wmcNotice / wmcNotice2, data=공지 번호). 여러 개가 겹쳐 뜰 수 있음
WMC_NOTICE_SEL = '[name="wmcNotice"], .wmcNotice, .wmcNotice2'

# 실제 WMC 공지 구조 (2026-11-09 캡처, wds.samsung.com/wds/sso/login/ssoLoginSuccess.do):
#   <div data="29" name="wmcNotice" class="wmcNotice2"> ... 공지 본문 ...
#     <div class="noticeBottom">
#       <div name="close" class="noticeBottomBtn noticeBtnClick"><b><font color="black">close</font></b></div>
#       <div class="noticeTodayWeek"> close today | close a week </div>
# → 'close' 버튼(name="close")만 누른다. 'close today' / 'close a week' 은 다음 로그인 공지 노출 설정을 바꾸므로 누르지 않음
WMC_NOTICE_CLOSE_SEL = '[name="close"].noticeBtnClick, .noticeBottomBtn.noticeBtnClick, [name="close"]'

# 공지 안의 '닫기' 버튼: 위 실제 선택자 우선, 없으면 텍스트(close/닫기) / class 로 찾음
JS_CLOSE_WMC_NOTICES = r"""([sel, closeSel]) => {
  const vis = e => { if (!e) return false; const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return r.height > 0 && r.width > 0 && cs.display !== 'none' && cs.visibility !== 'hidden'; };  // position:fixed 는 offsetParent 가 null
  const notices = [...document.querySelectorAll(sel)].filter(vis);
  let closed = 0;
  for (const n of notices) {
    const t = b => (b.innerText || b.getAttribute('title') || b.getAttribute('alt') || '').replace(/\s+/g, ' ').trim();
    const skip = b => /오늘|하루|일주일|다시|today|week|again|never/i.test(t(b)) || !!b.closest('.noticeTodayWeek');
    const exact = [...n.querySelectorAll(closeSel)].filter(vis).find(b => !skip(b));
    const btns = [...n.querySelectorAll('a, button, [onclick], [class*="close" i], [class*="btn" i]')].filter(vis);
    const close = exact
               || btns.find(b => !skip(b) && /^(닫기|close|x|×|확인|ok)$/i.test(t(b)))
               || btns.find(b => !skip(b) && /close/i.test(b.className || ''));
    if (close) { close.click(); closed++; }
  }
  return {visible: notices.length, closed};
}"""


async def dismiss_wmc_notices(page) -> int:
    """WMC(WDS) 로그인 후 뜨는 공지 팝업 레이어를 닫는다. goLink 클릭을 가로채므로 클릭 전에 호출.

    2026-11-09 p6-pre-qa3 실행에서 6번 중 5번의 goLink 클릭이 이 레이어 때문에 Timeout 났다.
    1) 공지마다 '닫기' 버튼을 DOM click (오늘 하루 보지 않기 류는 누르지 않음)
    2) 그래도 남아 있는 공지는 화면에서 숨김 (공지 내용은 검증 대상이 아님)
    반환: 처리한 공지 수
    """
    try:
        try:   # 공지는 로그인 직후 조금 늦게 뜨기도 함 → 잠깐만 기다림 (없으면 바로 진행)
            await page.locator(WMC_NOTICE_SEL).first.wait_for(state="visible", timeout=3000)
        except Exception:
            pass
        info = await page.evaluate(JS_CLOSE_WMC_NOTICES, [WMC_NOTICE_SEL, WMC_NOTICE_CLOSE_SEL])
        hidden = await page.evaluate(
            """(sel) => {
                const vis = e => { if (!e) return false; const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return r.height > 0 && r.width > 0 && cs.display !== 'none' && cs.visibility !== 'hidden'; };  // position:fixed 는 offsetParent 가 null
                const left = [...document.querySelectorAll(sel)].filter(vis);
                left.forEach(n => n.style.setProperty('display', 'none', 'important'));
                // 공지와 함께 뜨는 반투명 배경(dim)도 클릭을 가로챌 수 있음
                document.querySelectorAll('[class*="dim" i], [class*="overlay" i], [class*="mask" i]').forEach(d => {
                    const cs = getComputedStyle(d);
                    if (vis(d) && (cs.position === 'fixed' || cs.position === 'absolute') &&
                        d.getBoundingClientRect().width >= window.innerWidth * 0.8 && !d.querySelector('a[href*="goLink"]'))
                        d.style.setProperty('display', 'none', 'important');
                });
                return left.length;
            }""", WMC_NOTICE_SEL)
    except Exception as error:   # 페이지 이동 중 등 → 클릭 단계에서 다시 판단
        logging.debug("[SESSION] WMC 공지 처리 실패: %s", type(error).__name__)
        return 0
    if info.get("visible") or hidden:
        logging.info("[SESSION] WMC 공지 팝업 %d개 (닫기 %d, 숨김 %d)", info.get("visible", 0), info.get("closed", 0), hidden)
    return info.get("visible", 0)


def golink_href(is_cn: bool) -> str:
    return "javascript:goLink('p6-preqa3-cn');" if is_cn else "javascript:goLink('p6-preqa3');"


# ------------------------------------------------------------------ 세션 (BrowserContext 1개)
class FsSession:
    """국가(Target) 1건의 BrowserContext + 로그인 세션."""

    def __init__(self, context, target):
        self.context = context
        self.target = target
        self.generation = 0          # 세션을 새로 받을 때마다 +1
        self._lock = asyncio.Lock()
        self._wds_page = None

    # ---------------- 로그인 (p6-pre-qa3 만)
    async def login(self):
        if not self.target.needs_login:
            return
        from login.WDS_login_FS_Auto import samsung_login_and_move   # 라이브에서는 import 하지 않음

        page = await self.context.new_page()
        if not await samsung_login_and_move(page, country_code=self.target.site):
            raise SessionError("WDS 로그인 실패 (login/.env 의 LOGIN_ID_Auto / LOGIN_PW_Auto 확인 필요)")
        self._wds_page = page
        try:
            ok = await self._golink()
        except Exception as error:   # goLink 버튼 없음 / 새 탭 안 열림 등
            raise SessionError(f"WDS 로그인 후 goLink 실패 ({type(error).__name__}: "
                               f"{str(error).splitlines()[0] if str(error) else ''})") from error
        if not ok:
            raise SessionError("WDS 로그인 후 goLink 로 받은 STG 탭이 AEM 로그인 화면")
        self.generation += 1
        logging.info("[SESSION] %s %s 로그인 + FS 세션 확보", self.target.env_label, self.target.site)

    async def _golink(self) -> bool:
        """WDS 화면의 goLink 클릭 → 새 STG 탭이 AEM 로그인이 아니면 True. 확인 후 탭은 닫는다."""
        link = self._wds_page.locator(f'a[href="{golink_href(self.target.is_cn)}"]:visible').first
        await dismiss_wmc_notices(self._wds_page)
        # 공지가 별도 창으로 뜨는 경우에도 STG 탭만 받도록 URL 로 거른다 (about:blank → 이동 중인 탭 포함)
        async with self.context.expect_page(
                predicate=lambda pg: QA_HOST_MARK in pg.url or pg.url in ("", "about:blank"),
                timeout=TIMEOUTS["popup"]) as info:
            await link.click(timeout=10000)
        fs_page = await info.value
        try:
            await fs_page.wait_for_load_state("domcontentloaded", timeout=TIMEOUTS["page_load"])
            return not await page_is_aem_login(fs_page)
        finally:
            await fs_page.close()

    async def _relogin(self) -> bool:
        from login.WDS_login_FS_Auto import samsung_login_and_move

        if self._wds_page is None or self._wds_page.is_closed():
            self._wds_page = await self.context.new_page()
        if not await samsung_login_and_move(self._wds_page, country_code=self.target.site):
            return False
        return await self._golink()

    # ---------------- 세션 재획득 (GNB recover_fs_session 과 같은 순서)
    async def recover(self, seen_generation: int):
        async with self._lock:
            if self.generation != seen_generation:      # 다른 워커가 이미 새로 받음
                return
            logging.warning("[SESSION] %s%s 공유 세션 만료 → WDS goLink 재클릭", self.target.env_label,
                            " CN" if self.target.is_cn else "")
            ok = False
            try:
                ok = await self._golink()
            except Exception as error:   # goLink 버튼 없음 / 새 탭 안 열림 → 재로그인으로 넘어감
                logging.warning("[SESSION] goLink 재클릭 실패: %s", type(error).__name__)
            if not ok:
                logging.warning("[SESSION] goLink 갱신 실패 → WDS 재로그인")
                if not await self._relogin():
                    raise SessionError("WDS 재로그인 후에도 세션을 받지 못함")
            self.generation += 1
            logging.info("[SESSION] 세션 재획득 완료 (세대 %d)", self.generation)

    # ---------------- 페이지 이동 (세션 확인 포함)
    async def goto(self, page, url):
        """url 로 이동하고 메인 문서 응답을 돌려준다. QA 에서 AEM 로그인이면 세션을 다시 받고 1회 재시도."""
        for attempt in (1, 2):
            seen = self.generation
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])
            if not await page_is_aem_login(page):
                return response
            if not self.target.needs_login:
                raise SessionError(f"라이브 페이지가 로그인 화면으로 이동함: {page.url}")
            if attempt == 2:
                raise SessionError(f"세션 재획득 후에도 로그인 화면: {page.url}")
            await self.recover(seen)
            if page.context is not self.context:     # 워커 Context 에 새 쿠키 반영 (값은 로그에 남기지 않음)
                await page.context.add_cookies(await self.context.cookies())
        return None

    @asynccontextmanager
    async def worker_context(self):
        """사업부 워커용 Context. 같은 국가·환경 세션(쿠키)만 복사하고 localStorage 등 화면 상태는 분리한다."""
        browser = self.context.browser
        ctx = await browser.new_context(viewport=VIEWPORT, storage_state=await self.context.storage_state())
        try:
            yield ctx
        finally:
            await ctx.close()


class CountrySession:
    """국가 1건이 쓰는 세션 뷰. 로그인 세션(FsSession)은 환경마다 1개를 모든 국가가 공유한다.

    - 페이지 이동 / 세션 만료 감지 / 재획득은 공유 FsSession 으로 위임 (lock + 세대 번호 → 동시 만료여도 재획득 1회)
    - 사업부 워커 Context 는 공유 로그인 Context 의 쿠키를 복사해 국가마다 따로 만든다 (화면 상태 간섭 없음)
    """

    def __init__(self, auth, target):
        self.auth = auth
        self.target = target
        self.context = auth.context

    @property
    def generation(self):
        return self.auth.generation

    async def recover(self, seen_generation: int):
        logging.info("[SESSION] %s 에서 세션 만료 감지 → 공유 로그인 세션 재획득 요청", self.target.site)
        await self.auth.recover(seen_generation)

    async def goto(self, page, url):
        """FsSession.goto 와 같은 흐름. 대상 국가(target) 기준으로 판단하고 재획득은 공유 세션에서 한다."""
        for attempt in (1, 2):
            seen = self.generation
            response = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])
            if not await page_is_aem_login(page):
                return response
            if not self.target.needs_login:
                raise SessionError(f"라이브 페이지가 로그인 화면으로 이동함: {page.url}")
            if attempt == 2:
                raise SessionError(f"세션 재획득 후에도 로그인 화면: {page.url}")
            await self.recover(seen)
            await page.context.add_cookies(await self.auth.context.cookies())   # 값은 로그에 남기지 않음
        return None

    @asynccontextmanager
    async def worker_context(self):
        browser = self.auth.context.browser
        ctx = await browser.new_context(viewport=VIEWPORT, storage_state=await self.auth.context.storage_state())
        try:
            yield ctx
        finally:
            await ctx.close()


class SessionPool:
    """실행 전체에서 브라우저 1개 + 환경별 로그인 세션 1개 (2026-11-09: 로그인 1회 후 국가 간 세션 재사용).

    키 = (환경, CN 여부). p6-pre-qa3 일반 국가는 모두 같은 WDS 로그인 + goLink('p6-preqa3') 쿠키를 쓰고,
    CN 은 goLink('p6-preqa3-cn') 이 달라서 별도 세션. Live 는 로그인 없이 빈 Context 1개를 공유한다.
    로그인 실패 시 캐시하지 않음 → 다음 국가(또는 재시도)에서 다시 로그인을 시도한다.
    """

    def __init__(self, playwright, headless=True):
        self.playwright = playwright
        self.headless = headless
        self.browser = None
        self.logins = 0                  # 실제로 로그인한 횟수 (로그/검증용)
        self._auth = {}
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self.browser is None or not self.browser.is_connected():
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
        return self.browser

    async def session_for(self, target) -> CountrySession:
        key = (target.env, target.is_cn)
        async with self._lock:           # 첫 국가들이 동시에 와도 로그인은 1번
            auth = self._auth.get(key)
            if auth is None or auth.context.browser is None or not auth.context.browser.is_connected():
                browser = await self._ensure_browser()
                context = await browser.new_context(viewport=VIEWPORT)
                auth = FsSession(context, target)
                try:
                    await auth.login()
                except Exception:
                    await context.close()
                    raise
                if target.needs_login:
                    self.logins += 1
                    logging.info("[SESSION] %s%s 로그인 세션 생성 (이후 국가는 이 세션 재사용)",
                                 target.env_label, " CN" if target.is_cn else "")
                self._auth[key] = auth
            else:
                logging.info("[SESSION] %s %s 기존 로그인 세션 재사용 (세대 %d)",
                             target.env_label, target.site, auth.generation)
        return CountrySession(auth, target)

    async def close(self):
        if self.browser is not None:
            await self.browser.close()


@asynccontextmanager
async def open_session(playwright, target, headless=True):
    """Target 1건용 브라우저 + Context 를 열고 (QA 면 로그인) 끝나면 정리한다."""
    browser = await playwright.chromium.launch(headless=headless)
    try:
        context = await browser.new_context(viewport=VIEWPORT)
        session = FsSession(context, target)
        await session.login()
        yield session
    finally:
        await browser.close()
