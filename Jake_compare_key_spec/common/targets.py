# -*- coding: utf-8 -*-
"""URL/base_urls.xlsx → 실행 대상 (국가 + 환경).

New_GNB_Code_Update 와 같은 규칙:
  - 첫 번째 시트, 'base_url' 열, 한 줄에 URL 하나 (GNB load_base_urls_from_excel)
  - 국가 = URL path 첫 구간 (GNB extract_country_code 의 path 규칙)
  - 환경 = URL host (별도 Environment 열 없음). 판별은 detect_env() 한 곳에서만 한다.

URL Excel 에 적힌 국가만 대상이 된다. 비었거나 잘못된 줄이 있으면 전체 국가나
기본 국가로 대신하지 않고 TargetError 로 멈춘다.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import openpyxl

from common.config import ENV_LABEL, ENV_LIVE, ENV_P6_PRE_QA3, LIVE_HOST, QA_HOST_MARK, URL_COLUMN

# GNB_Checker.LOCALE_PATH_RE 와 같은 규칙: uk / latin / africa_en / n_africa ...
LOCALE_PATH_RE = re.compile(
    r"^[a-z]{2}$|^(?:latin|iran|levant)$|^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)+$",
    re.IGNORECASE,
)


class TargetError(Exception):
    """URL Excel 이 없거나 비었거나, 확인이 필요한 줄이 있음."""


@dataclass(frozen=True)
class Target:
    row: int
    url: str        # 엑셀에 적힌 URL (결과의 '진행URL')
    site: str       # 사이트코드 (소문자, 예: uk / ae_ar)
    env: str        # ENV_LIVE / ENV_P6_PRE_QA3
    host: str       # https://www.samsung.com 형태

    @property
    def needs_login(self) -> bool:
        return self.env == ENV_P6_PRE_QA3

    @property
    def env_label(self) -> str:
        return ENV_LABEL[self.env]

    @property
    def is_cn(self) -> bool:
        return self.site == "cn"


def detect_env(url: str):
    """host 로 환경 판별. 지원하지 않으면 None."""
    host = (urlparse(url).hostname or "").lower()
    if host == LIVE_HOST:
        return ENV_LIVE
    if QA_HOST_MARK in host:
        return ENV_P6_PRE_QA3
    return None


def extract_site_code(url: str):
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and LOCALE_PATH_RE.fullmatch(parts[0]):
        return parts[0].lower()
    return None


def read_base_urls(path):
    """첫 시트 base_url 열 → [(행, 값)] (빈 칸 포함, 끝의 빈 줄은 제외)."""
    path = Path(path)
    if not path.exists():
        raise TargetError(f"URL Excel 이 없습니다: {path}")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [str(h or "").strip() for h in (next(rows, None) or [])]
        if URL_COLUMN not in header:
            raise TargetError(f"{path.name} 첫 시트에 '{URL_COLUMN}' 열이 없습니다. (헤더: {header})")
        col = header.index(URL_COLUMN)
        out = []
        for n, r in enumerate(rows, start=2):
            value = r[col] if col < len(r) else None
            other = any(v not in (None, "") for i, v in enumerate(r) if i != col)
            out.append((n, str(value or "").strip(), other))
        while out and not out[-1][1] and not out[-1][2]:   # 끝의 완전히 빈 줄
            out.pop()
        return [(n, v) for n, v, _ in out]
    finally:
        wb.close()


def load_targets(path):
    """URL Excel → [Target]. 잘못된 줄이 하나라도 있으면 TargetError."""
    entries = read_base_urls(path)
    if not entries:
        raise TargetError(f"{Path(path).name} 에 실행할 URL 이 없습니다. (전체 국가를 대신 실행하지 않음)")

    targets, problems, seen = [], [], {}
    for row, url in entries:
        if not url:
            problems.append(f"  {row}행: URL 공백")
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            problems.append(f"  {row}행: 잘못된 URL '{url}'")
            continue
        env = detect_env(url)
        if env is None:
            problems.append(f"  {row}행: 지원하지 않는 환경 '{parsed.hostname}' ({LIVE_HOST} / {QA_HOST_MARK} 만 지원)")
            continue
        site = extract_site_code(url)
        if site is None:
            problems.append(f"  {row}행: URL path 에 국가코드가 없음 '{url}'")
            continue
        key = (env, site)
        if key in seen:
            problems.append(f"  {row}행: 국가 중복 ({ENV_LABEL[env]} {site}, {seen[key]}행과 같음)")
            continue
        seen[key] = row
        targets.append(Target(row=row, url=url, site=site, env=env,
                              host=f"{parsed.scheme}://{parsed.hostname}"))

    if problems:
        raise TargetError("URL Excel 에 확인이 필요한 줄이 있어 실행하지 않습니다.\n" + "\n".join(problems))
    return targets
