"""driver_utils 의 드라이버/브라우저 경로 해석 — 네트워크·Chrome 기동 없이 순수 검증.

배경(2026-07-08 prod 장애): create_chrome_driver 가 항상 webdriver_manager 로 드라이버를
런타임 다운로드했다. wdm 은 브라우저를 major.minor.build 까지만 보고 그 빌드의 최신 패치를
받아오므로 패치 번호가 어긋난다(chromium 150.0.7871.46 ↔ chromedriver 150.0.7871.49) →
SessionNotCreatedException: Chrome instance exited → CronJob exit 1.

이미지에는 chromium 과 같은 apt 트랜잭션에서 설치돼 **버전이 일치하는** chromedriver 가
번들돼 있다. 그것을 먼저 쓰게 하는 것이 이 테스트가 박제하는 계약이다.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from app.collectors.hiring import driver_utils
from app.collectors.hiring.driver_utils import (
    resolve_chrome_binary,
    resolve_chromedriver_path,
)


class ResolveChromedriverPathTest(unittest.TestCase):
    def test_prefers_chromedriver_path_env(self):
        with patch.dict(os.environ, {"CHROMEDRIVER_PATH": "/opt/cd"}), \
             patch.object(driver_utils.os.path, "isfile", return_value=True), \
             patch.object(driver_utils.os, "access", return_value=True):
            self.assertEqual(resolve_chromedriver_path(), "/opt/cd")

    def test_falls_back_to_default_debian_path(self):
        env = dict(os.environ)
        env.pop("CHROMEDRIVER_PATH", None)
        with patch.dict(os.environ, env, clear=True), \
             patch.object(driver_utils.os.path, "isfile", lambda p: p == "/usr/bin/chromedriver"), \
             patch.object(driver_utils.os, "access", return_value=True):
            self.assertEqual(resolve_chromedriver_path(), "/usr/bin/chromedriver")

    def test_none_when_nothing_installed(self):
        """번들이 없으면 None → 호출부가 webdriver_manager 로 폴백(로컬 개발 회귀 방지)."""
        with patch.object(driver_utils.os.path, "isfile", return_value=False):
            self.assertIsNone(resolve_chromedriver_path())

    def test_env_pointing_at_missing_file_is_ignored(self):
        """CHROMEDRIVER_PATH 가 있어도 파일이 없으면 채택하지 않는다(없는 경로로 Service 금지)."""
        with patch.dict(os.environ, {"CHROMEDRIVER_PATH": "/nope/cd"}), \
             patch.object(driver_utils.os.path, "isfile", return_value=False):
            self.assertIsNone(resolve_chromedriver_path())

    def test_non_executable_is_ignored(self):
        with patch.dict(os.environ, {"CHROMEDRIVER_PATH": "/opt/cd"}), \
             patch.object(driver_utils.os.path, "isfile", return_value=True), \
             patch.object(driver_utils.os, "access", return_value=False):
            self.assertIsNone(resolve_chromedriver_path())


class ResolveChromeBinaryTest(unittest.TestCase):
    def test_uses_chrome_bin_when_set(self):
        with patch.dict(os.environ, {"CHROME_BIN": "/usr/bin/chromium"}), \
             patch.object(driver_utils.os.path, "isfile", return_value=True), \
             patch.object(driver_utils.os, "access", return_value=True):
            self.assertEqual(resolve_chrome_binary(), "/usr/bin/chromium")

    def test_none_without_chrome_bin(self):
        """로컬에서는 selenium 기본 탐색에 맡긴다 — chromium 을 임의로 집지 않는다."""
        env = dict(os.environ)
        env.pop("CHROME_BIN", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(resolve_chrome_binary())


class CreateChromeDriverSelectionTest(unittest.TestCase):
    """create_chrome_driver 가 어느 드라이버를 고르는지 — Chrome 은 띄우지 않는다."""

    def _run(self, bundled: str | None, chrome_bin: str | None = None):
        fake_driver = MagicMock()
        with patch.object(driver_utils, "resolve_chromedriver_path", return_value=bundled), \
             patch.object(driver_utils, "resolve_chrome_binary", return_value=chrome_bin), \
             patch("selenium.webdriver.Chrome", return_value=fake_driver) as chrome, \
             patch("selenium.webdriver.chrome.service.Service") as service:
            driver_utils.create_chrome_driver(headless=True)
        return chrome, service

    def test_bundled_driver_used_without_download(self):
        """번들이 있으면 webdriver_manager 를 import 조차 하지 않는다(네트워크 0)."""
        with patch.dict("sys.modules", {"webdriver_manager.chrome": None}):
            chrome, service = self._run(bundled="/usr/bin/chromedriver")
        service.assert_called_once_with(executable_path="/usr/bin/chromedriver")
        self.assertIn("service", chrome.call_args.kwargs)

    def test_chrome_binary_is_pinned_when_available(self):
        chrome, _ = self._run(bundled="/usr/bin/chromedriver", chrome_bin="/usr/bin/chromium")
        opts = chrome.call_args.kwargs["options"]
        self.assertEqual(opts.binary_location, "/usr/bin/chromium")

    def test_falls_back_to_webdriver_manager_when_no_bundle(self):
        fake_driver = MagicMock()
        manager = MagicMock()
        manager.return_value.install.return_value = "/tmp/downloaded/chromedriver"
        with patch.object(driver_utils, "resolve_chromedriver_path", return_value=None), \
             patch.object(driver_utils, "resolve_chrome_binary", return_value=None), \
             patch.dict("sys.modules", {"webdriver_manager.chrome": MagicMock(
                 ChromeDriverManager=manager)}), \
             patch("selenium.webdriver.Chrome", return_value=fake_driver), \
             patch("selenium.webdriver.chrome.service.Service") as service:
            driver_utils.create_chrome_driver(headless=True)
        manager.return_value.install.assert_called_once()
        service.assert_called_once_with("/tmp/downloaded/chromedriver")


if __name__ == "__main__":
    unittest.main()
