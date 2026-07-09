"""
driver_utils.py
Chrome WebDriver 생성 팩토리 — Anti-Bot + 안정성 옵션 중앙화

WebCrawler / MultiSourceCrawler 에서 중복 구현되던 _setup_driver() 를 단일화.
DRY 원칙: 드라이버 옵션 변경 시 이 파일 한 곳만 수정.

드라이버 해석 순서 (컨테이너 결정론):
  이미지에 번들된 chromedriver(CHROMEDRIVER_PATH, 기본 /usr/bin/chromedriver)를 **먼저** 쓰고,
  없을 때만 webdriver_manager 로 폴백한다. 로컬 개발자는 번들이 없으므로 기존 동작 유지.

  왜: Dockerfile.crawler 가 `apt-get install chromium chromium-driver` 로 둘을 한 트랜잭션에
  설치하므로 버전이 항상 일치한다(실측: 둘 다 150.0.7871.100). 반면 webdriver_manager 는
  브라우저를 major.minor.build(150.0.7871)까지만 보고 **그 빌드의 최신 패치**를 내려받아
  패치 번호가 어긋난다. 2026-07-08 프로덕션 CronJob 이 이것으로 죽었다:
      chromium 150.0.7871.46  ←→  wdm 이 받아온 chromedriver 150.0.7871.49
      SessionNotCreatedException: Chrome instance exited  → 4회 재시도 후 exit 1
  번들 드라이버를 쓰면 (1) 버전 불일치가 구조적으로 불가능하고, (2) 런타임에
  storage.googleapis.com 을 때리지 않아 네트워크 차단/장애에도 안전하며, (3) 같은 이미지가
  항상 같게 동작한다.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 데비안 chromium-driver 의 설치 경로. Dockerfile.crawler 가 CHROMEDRIVER_PATH 로도 노출한다.
_DEFAULT_CHROMEDRIVER = "/usr/bin/chromedriver"


def resolve_chromedriver_path() -> str | None:
    """번들 chromedriver 의 실행 경로. 없으면 None(→ 호출부가 webdriver_manager 폴백).

    순수 함수(네트워크·드라이버 기동 없음) — 테스트에서 직접 호출한다.
    CHROMEDRIVER_PATH 가 가리키는 파일이 실제로 실행 가능해야 채택한다. 값이 있어도 파일이
    없으면(오타·이미지 변경) 폴백해야지, 존재하지 않는 경로로 Service 를 띄우면 안 된다.
    """
    for candidate in (os.getenv("CHROMEDRIVER_PATH"), _DEFAULT_CHROMEDRIVER):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_chrome_binary() -> str | None:
    """Chrome/Chromium 실행 파일 경로. 없으면 None(→ selenium 기본 탐색).

    **CHROME_BIN 이 명시된 경우에만** 고정한다(컨테이너). 로컬에서 chromium 을 자동으로
    집어 올리면 Chrome 을 쓰던 개발자의 동작이 바뀌므로, 탐색은 selenium 에 맡긴다.
    """
    candidate = os.getenv("CHROME_BIN")
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def create_chrome_driver(headless: bool = True):
    """
    Chrome WebDriver 인스턴스 생성 및 반환.

    안정성 옵션:
      --disable-gpu           헤드리스 환경의 GPU 초기화 실패 크래시 방지
      --disable-dev-shm-usage /dev/shm 메모리 부족 방지 (컨테이너/Windows 공통)
      --window-size=1920,1080 뷰포트 고정 (레이아웃 파싱 안정화)

    Anti-Bot:
      --disable-blink-features=AutomationControlled  ← 자동화 플래그 숨김
      navigator.webdriver 프로퍼티 은닉 (CDP 명령)
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")

    # ── 안정성 ────────────────────────────────────────────────────────────────
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-plugins")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--blink-settings=imagesEnabled=false")

    # ── Anti-Bot ──────────────────────────────────────────────────────────────
    # UA는 풀에서 로테이션해 핑거프린트를 분산한다. 단, requests(sites/http.py)는
    # 매 시도마다 UA를 교체하는 반면 Selenium UA는 이 드라이버 인스턴스 수명 동안
    # 고정된다(_safe_get 재시도는 동일 UA로 백오프만). 드라이버 로테이션 주기마다
    # 새 UA로 갱신된다.
    from .user_agents import pick_ua

    opts.add_argument(f"user-agent={pick_ua()}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # ── 브라우저 바이너리 고정 (컨테이너에서만) ─────────────────────────────────
    chrome_binary = resolve_chrome_binary()
    if chrome_binary:
        opts.binary_location = chrome_binary

    # ── 드라이버 해석: 번들 우선 → webdriver_manager 폴백 ──────────────────────
    # 번들(chromium-driver)은 chromium 과 같은 apt 트랜잭션에서 나와 버전이 항상 일치한다.
    # wdm 은 런타임 다운로드라 패치 번호가 어긋날 수 있다(모듈 docstring 의 프로덕션 장애).
    bundled = resolve_chromedriver_path()
    if bundled:
        logger.info("✓ 번들 chromedriver 사용: %s (다운로드 없음)", bundled)
        driver = webdriver.Chrome(service=Service(executable_path=bundled), options=opts)
    else:
        # 로컬 개발 환경 — 번들이 없으므로 기존 동작(런타임 다운로드) 유지.
        logger.info("ℹ️  번들 chromedriver 없음 → webdriver_manager 폴백")
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=opts)
        except Exception:
            driver = webdriver.Chrome(options=opts)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    logger.info("✓ Chrome WebDriver 초기화 완료 (Anti-Bot 강화, headless=%s)", headless)
    return driver
