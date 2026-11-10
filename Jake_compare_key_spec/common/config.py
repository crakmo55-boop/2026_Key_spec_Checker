# -*- coding: utf-8 -*-
"""Key spec check 공통 설정 (New_GNB_Code_Update 처럼 상수로 관리)."""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ 폴더 / 파일
URL_FILE = ROOT_DIR / "URL" / "base_urls.xlsx"
URL_COLUMN = "base_url"                        # GNB 와 같은 열 이름 (첫 시트)
MAPPING_FILE = ROOT_DIR / "Docs" / "All key spec Check.xlsx"
RESULT_DIR = ROOT_DIR / "Result"
SCRATCH_DIR = ROOT_DIR / "Scratch"             # 수동 디버그/분석 스크립트 보관 (실행 흐름에서 사용하지 않음)

# 결과 파일: 실행할 때마다 새 파일 (<이름>_YYYYMMDD_HHMMSS.xlsx). 이전 결과를 지워도 매번 새로 만든다.
# 아래 경로는 '이름 + 폴더' 기준이며, 실제 파일명은 common/result.py ResultWriter 가 실행 시각을 붙여 정한다.
LANDING_OUTPUT = RESULT_DIR / "2026_KeySpec_Landing.xlsx"
POPUP_OUTPUT = RESULT_DIR / "2026_KeySpec_Popup.xlsx"

# ------------------------------------------------------------------ 환경
ENV_LIVE = "LIVE"
ENV_P6_PRE_QA3 = "P6_PRE_QA3"
LIVE_HOST = "www.samsung.com"
QA_HOST_MARK = "p6-pre-qa3"
ENV_LABEL = {ENV_LIVE: "Live", ENV_P6_PRE_QA3: "p6-pre-qa3"}

# True=브라우저 화면 표시, False=headless (GNB 의 HEADED 와 같은 방식)
HEADED = False
VIEWPORT = {"width": 1920, "height": 1080}     # GNB 와 같은 PC 해상도
DEFAULT_RETRIES = 2                            # GNB DEFAULT_URL_RETRIES 와 동일 (첫 시도 제외)
# 동시에 처리할 국가 수 (2026-11-09). 국가마다 사업부 3개가 병렬 → 동시 탭 최대 COUNTRY_PARALLEL x 3.
# 로그인은 환경별 1회만 하고 모든 국가가 그 세션을 재사용한다 (common/session.SessionPool).
COUNTRY_PARALLEL = 3

# ------------------------------------------------------------------ 매핑 파일
DIVISIONS = ("MX", "VD", "DA")                 # 시트 이름 = 사업부
MAPPING_COLUMNS = ("SITE_CD", "IA_CD", "CATEGORY_NAME", "FMY_ID", "FMY_ENG_NM",
                   "FMY_MKT_NM", "REP_MODEL", "DISPLAY_STATUS")
SET_SIZE = 3

# 최신/중간 제품 판별 규칙 (common/mapping.py RULES 의 이름).
# "category_fmy_id": 사업부·Set 마다 정해진 카테고리 안에서 FMY_ID 순서로 선정 (사용자 지정, 2026-11-09 피드백)
# "fmy_id"         : (이전 규칙) 사업부 전체에서 FMY_ID 최대 제품의 PF 묶음 범위
# None 이면 선정하지 않고 '확인 필요'.
PRODUCT_SET_RULE = "category_fmy_id"

# 사업부별 Set 카테고리 (모든 국가, Landing / Popup 공통). 값 = CATEGORY_SPECS 페이지 키
SET_CATEGORIES = {
    "MX": {"Latest": "smartphones", "Middle": "tablets"},
    "VD": {"Latest": "tvs", "Middle": "tvs"},
    "DA": {"Latest": "refrigerators", "Middle": "laundry"},
}
# 화면(PF / Landing 드롭다운)에 없는 제품은 같은 Set 규칙의 다음 후보로 바꿔 다시 찾는다 (2026-11-09 피드백).
# Set 당 최대 몇 개 후보까지 시도할지. 0 이면 대체하지 않고 '확인 필요'.
MAX_ALTERNATES = 10

# Key Spec 대체 (2026-11-10 사용자 지시): 요청 제품에 Key Specs 가 없으면(열 값 없음 / 섹션 미노출 / 새 팝업 미표시)
# 매핑에서 같은 사업부·같은 Set 카테고리의 다른 제품을 골라 다시 확인한다. 판정 규칙(기존 Key spec 코드)은 그대로이고
# '어떤 제품을 검증할지' 만 바꾼다. 원래 제품과 사유는 비고·로그([KEYSPEC-대체])에 남겨 결함 추적이 가능하게 한다.
KEYSPEC_SUBSTITUTE_ROUNDS = 6        # Set 당 Compare 를 다시 여는 최대 횟수 (0 이면 대체하지 않음)
SET_ATTEMPTS = 2                     # Set 처리 중 예외(페이지 이동으로 컨텍스트 소멸 등) 시 새 Context 로 다시 시도하는 횟수
POPUP_SET_BUDGET_S = 300             # Popup Set 1개 최대 처리 시간 (초과하면 대체 시도를 멈추고 그때 결과로 기록)
LANDING_SET_BUDGET_S = 300           # Landing Set 1개 최대 처리 시간
LANDING_SELECT_BUDGET_S = 75         # Landing 열 1개에 제품 1개를 고르는 최대 시간 (옵션 탐색 포함)
LANDING_SCAN_MAX = 25                # 이름으로 못 찾을 때 드롭다운 옵션을 직접 골라 SKU 를 확인하는 최대 개수
DIAG_SCREENSHOTS = 150               # 실패 진단 스크린샷 최대 개수 (실행당, Result/logs/<로그이름>_diag/)

# Landing 이 Popup 과 같은 제품을 검증하도록 (2026-11-10 결정 7-4).
# True 면 Landing 실행 시 Result 폴더의 최신 Popup 결과(아래 시간 이내)를 읽어, Popup 에서 SKU 까지 확정(SKU 매칭 PASS)된
# 3개 제품을 그대로 사용한다. Popup 결과가 없거나 3개가 안 되는 Set 은 Landing 이 직접 고른다.
USE_POPUP_PRODUCTS = True
POPUP_PRODUCTS_MAX_AGE_HOURS = 24

# 페이지 키 → 매핑 파일 IA_CD 앞 4자리 (CATEGORY_NAME 과 대응)
#   0101 Mobile > Smartphones / 0102 Mobile > Tablets / 0401 Television > TVs
#   0803 Home Appliances > Refrigerators / 0801 Home Appliances > Washers and Dryers
PAGE_IA_PREFIX = {
    "smartphones": "0101",
    "tablets": "0102",
    "tvs": "0401",
    "refrigerators": "0803",
    "laundry": "0801",
}

# ------------------------------------------------------------------ 페이지
# 기존 Compare-main/compare/config.py 의 CATEGORY_SPECS 그대로
# (그룹, 페이지 키, 화면 라벨, PF 경로, Compare 랜딩 경로)
CATEGORY_SPECS = [
    ("MX", "smartphones", "Smartphones", "smartphones/all-smartphones/", "smartphones/compare/"),
    ("MX", "tablets", "Tablets", "tablets/all-tablets/", "tablets/compare/"),
    ("MX", "watches", "Watches", "watches/all-watches/", "watches/compare/"),
    ("MX", "buds", "Galaxy Buds", "audio-sound/all-audio-sound/", "audio-sound/compare/"),
    ("PC", "galaxy-book", "Galaxy Book", "computers/all-computers/", "galaxy-book/galaxy-book-series/compare/"),
    ("PC", "monitors", "Monitors", "monitors/all-monitors/", "monitors/compare/"),
    ("TV", "tvs", "TVs", "tvs/all-tvs/", "tvs/compare/"),
    ("Audio", "soundbars", "Audio Devices", "audio-devices/all-audio-devices/", "audio-devices/compare/"),
    ("DA", "refrigerators", "Refrigerators", "refrigerators/all-refrigerators/", "refrigerators/compare/"),
    ("DA", "laundry", "Washers & Dryers", "washers-and-dryers/all-washers-and-dryers/", "washers-and-dryers/compare/"),
    ("DA", "dishwashers", "Dishwashers", "dishwashers/all-dishwashers/", "dishwashers/compare/"),
    ("DA", "cooking", "Cooking Appliances", "cooking-appliances/ovens/", "cooking-appliances/compare/"),
    ("DA", "microwaves", "Microwave Ovens", "microwave-ovens/all-microwave-ovens/", "microwave-ovens/compare/"),
    ("DA", "vacuums", "Vacuum Cleaners", "vacuum-cleaners/all-vacuum-cleaners/", "vacuum-cleaners/compare/"),
]

# 매핑 시트(사업부) → CATEGORY_SPECS 페이지 키.
# 시트에 실제로 들어 있는 CATEGORY_NAME 기준: MX 시트 = Mobile + Computers(Galaxy Book),
# VD 시트 = Television + Audio + Display(Monitors), DA 시트 = Home Appliances
DIVISION_PAGES = {
    "MX": ("smartphones", "tablets", "watches", "buds", "galaxy-book"),
    "VD": ("tvs", "soundbars", "monitors"),
    "DA": ("refrigerators", "laundry", "dishwashers", "cooking", "microwaves", "vacuums"),
}

# ------------------------------------------------------------------ 대기 (ms) - 고정 대기가 아니라 조건 대기의 최대 시간
TIMEOUTS = {
    "page_load": 90000,      # goto
    "element": 60000,        # 주요 컴포넌트 등장
    "popup": 30000,          # Compare 팝업 표시
    "api": 30000,            # Compare API 응답
    "column_change": 20000,  # 드롭다운 선택 후 열 갱신
    "lazy_load": 10000,      # PF 'View more' 후 카드 추가 로딩
    "click": 10000,          # 버튼 클릭 (가림 요소 해소 대기 포함)
    "button_ready": 15000,   # PF 체크 후 Compare 버튼에 3개 SKU 가 반영되기까지
    "keyspec_settle": 6000,  # Compare API 응답 뒤 Key Specs 섹션이 그려지기까지
}

# ------------------------------------------------------------------ 결과 표시
RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_CHECK = "확인 필요"
PAGE_LANDING = "Landing Page"
PAGE_POPUP = "PF POPUP Page"
