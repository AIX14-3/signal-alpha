"""
driver_utils.py
Chrome WebDriver 생성 팩토리 — Anti-Bot + 안정성 옵션 중앙화

WebCrawler / MultiSourceCrawler 에서 중복 구현되던 _setup_driver() 를 단일화.
DRY 원칙: 드라이버 옵션 변경 시 이 파일 한 곳만 수정.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
