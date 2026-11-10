# -*- coding: utf-8 -*-
"""결과 행 + 단일 Excel Writer.

Workbook 구조·서식은 New_GNB_Code_Update/Result/2026_GNB_Check_Quick.xlsx 기준
  - 시트: Result_All + 국가별 시트(사이트코드 대문자)
  - 헤더: 굵게 + 가운데 정렬 (배경색 없음), 고정/필터 없음, 요약 시트 없음
  - 결과 열: PASS 초록(C6EFCE) / FAIL 빨강(FFC7CE, 글자 9C0006) / 확인 필요 노랑(FFD966, GNB 경고색)
  - HTTP 상태: 200 이 아니면 빨강
  - 첫 열 '국가' = 사이트코드 대문자 (예: UK). 환경(Live / p6-pre-qa3)은 실행 로그에 기록
  - 실행할 때마다 새 파일 '<이름>_YYYYMMDD_HHMMSS.xlsx'

병렬 워커는 행(dict)만 돌려주고, 저장은 메인 흐름의 ResultWriter 하나만 한다.
"""
import logging
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common.config import RESULT_CHECK, RESULT_FAIL, RESULT_PASS
from common.targets import extract_site_code

COLUMN_COUNTRY = "국가"
COLUMN_KEYSPEC = "Key spec 존재 결과"
COLUMN_SKU_MATCH = "SKU 매칭 결과"
# 2026-11-09 피드백: '페이지 타입' 삭제, '진행URL' → 국가명(UK), mapping table 텍스트 / SKU 매칭 결과 추가,
#                   '실제 제품 텍스트' 삭제
COLUMNS = [COLUMN_COUNTRY, "사업부", "Set", "구분",
           "mapping table 텍스트",              # All key spec Check.xlsx 의 FMY_MKT_NM
           "최종 페이지 랜딩 후 제품 텍스트",     # Compare 화면 열의 제품명
           "SKU",                               # mapping table REP_MODEL
           "최종 페이지 SKU",                    # Compare 화면 열의 SKU
           COLUMN_SKU_MATCH,                    # SKU == 최종 페이지 SKU → PASS / 다르면 FAIL / 화면 미도달 → 확인 필요
           "HTTP 상태", COLUMN_KEYSPEC, "비고"]
WIDTHS = [7, 8, 8, 9, 40, 40, 20, 20, 14, 11, 16, 60]
RESULT_COLUMNS = (COLUMN_SKU_MATCH, COLUMN_KEYSPEC)   # PASS/FAIL 색을 칠하는 열

_FILL_PASS = PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE")
_FILL_FAIL = PatternFill("solid", start_color="FFC7CE", end_color="FFC7CE")
_FILL_WARN = PatternFill("solid", start_color="FFD966", end_color="FFD966")
_FONT_FAIL = Font(color="9C0006")


def sku_match(expected, final_sku) -> str:
    """mapping table SKU 와 최종 페이지 SKU 비교 (대소문자·앞뒤 공백 무시).
    최종 SKU 를 읽지 못함('' / '-') = 비교 불가 → 확인 필요 (FAIL 로 쓰지 않음)."""
    if not expected or not final_sku or str(final_sku).strip() in ("-", ""):
        return RESULT_CHECK
    return RESULT_PASS if str(expected).strip().upper() == str(final_sku).strip().upper() else RESULT_FAIL


def country_name(target) -> str:
    return (getattr(target, "site", "") or extract_site_code(target.url) or "UNKNOWN").upper()


def make_row(target, page_type, product_set, product=None, *, final_text="", final_sku="",
             http=None, result=RESULT_CHECK, note="", sku_result=None):
    """page_type 은 결과 열에서 빠졌지만(파일이 Landing / Popup 으로 나뉨) 호출 형태는 유지한다.
    sku_result: 자동화가 제품을 화면에 띄우지 못해 다른 제품이 보이는 경우 등 '비교 대상이 아님' 을 표시할 때
                RESULT_CHECK 를 넘긴다 (사이트 SKU 불일치 FAIL 과 구분)."""
    expected = product.rep_model if product else ""
    return {
        COLUMN_COUNTRY: country_name(target),
        "사업부": product_set.division,
        "Set": product_set.set_no,
        "구분": product_set.kind,
        "mapping table 텍스트": product.fmy_mkt if product else "",
        "최종 페이지 랜딩 후 제품 텍스트": final_text,
        "SKU": expected,
        "최종 페이지 SKU": final_sku,
        COLUMN_SKU_MATCH: sku_result or sku_match(expected, final_sku),
        "HTTP 상태": http,
        COLUMN_KEYSPEC: result,
        "비고": note,
    }


def replaced_note(product, replaced) -> str:
    """대체로 들어간 제품이면 비고 앞에 붙일 문구.
    replaced 값: 원래 Product (화면에 없어 대체) 또는 (원래 Product, 사유 문자열) (Key spec 대체 등)"""
    item = replaced.get(product.rep_model) if product else None
    if not item:
        return ""
    orig, why = item if isinstance(item, tuple) else (item, "화면에 없음")
    tag = "[Key spec 대체]" if "Key Spec" in why or "팝업" in why else "[대체]"
    return f"{tag} 원래 제품 {orig.rep_model} ({orig.fmy_mkt}) {why} → 이 제품으로 대체 | "


def set_rows(target, page_type, product_set, result, note, http=None):
    """Set 전체에 같은 결과를 붙인다 (제품이 없으면 Set 당 1행)."""
    items = product_set.products or [None]
    return [make_row(target, page_type, product_set, p, http=http, result=result, note=note) for p in items]


class ResultWriter:
    """하나의 Writer 가 전체 행을 받아 Workbook 을 새로 쓴다 (부분 수정 없음 → 덮어쓰기/충돌 없음).

    실행할 때마다 새 결과 파일을 만든다: <이름>_YYYYMMDD_HHMMSS.xlsx (Writer 생성 시각).
    국가가 끝날 때마다 같은 파일에 전체 행을 다시 써서, 중간에 멈춰도 그때까지의 결과가 남는다.
    이전 결과 파일을 지워도 상관없음 (Result 폴더가 없으면 새로 만든다).
    """

    def __init__(self, output_file, run_started=None):
        base = Path(output_file)
        stamp = f"{(run_started or datetime.now()):%Y%m%d_%H%M%S}"
        self.output_file = base.with_name(f"{base.stem}_{stamp}{base.suffix}")

    def save(self, rows):
        if not rows:
            logging.warning("[SAVE] 저장할 결과가 없습니다.")
            return None
        wb = Workbook()
        ws = wb.active
        ws.title = "Result_All"
        self._fill(ws, rows)
        by_site = {}
        for r in rows:
            by_site.setdefault((r.get(COLUMN_COUNTRY) or "UNKNOWN")[:31], []).append(r)
        for name, site_rows in by_site.items():
            self._fill(wb.create_sheet(name), site_rows)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        target = self.output_file
        try:
            wb.save(target)
        except PermissionError:   # 실행 중 사용자가 결과 파일을 열어 둔 경우 → 이후 저장은 새 이름으로 계속
            target = target.with_name(f"{target.stem}_{datetime.now():%H%M%S}{target.suffix}")
            logging.warning("[SAVE] 파일이 열려 있어 %s 로 저장합니다.", target.name)
            wb.save(target)
            self.output_file = target
        logging.info("[SAVE] %d행 → %s (시트 %s)", len(rows), target, ", ".join(wb.sheetnames))
        return target

    @staticmethod
    def _fill(ws, rows):
        ws.append(COLUMNS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        res_cols = [COLUMNS.index(c) + 1 for c in RESULT_COLUMNS]
        http_col = COLUMNS.index("HTTP 상태") + 1
        for r in rows:
            ws.append([r.get(c) for c in COLUMNS])
            for col in res_cols:
                res = ws.cell(ws.max_row, col)
                if res.value == RESULT_PASS:
                    res.fill = _FILL_PASS
                elif res.value == RESULT_FAIL:
                    res.fill, res.font = _FILL_FAIL, _FONT_FAIL
                elif res.value:
                    res.fill = _FILL_WARN
            http = ws.cell(ws.max_row, http_col)
            if http.value not in (None, "") and str(http.value).strip() != "200":
                http.fill, http.font = _FILL_FAIL, _FONT_FAIL
        for i, w in enumerate(WIDTHS):
            ws.column_dimensions[get_column_letter(i + 1)].width = w
