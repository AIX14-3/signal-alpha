"""공식 채용관 진단 툴킷(script/diagnose_official_sites.py) 단위 테스트 (#175).

스크립트(패키지 아님)를 importlib로 로드해 TARGETS 무결성·바이트 저장·DNS 실패 처리를
검증한다. 실제 네트워크는 requests.get mock 으로 대체.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from unittest import mock

import requests

_SCRIPT = pathlib.Path(__file__).parents[1] / "script" / "diagnose_official_sites.py"
_spec = importlib.util.spec_from_file_location("diagnose_official_sites", _SCRIPT)
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)


class TargetsIntegrityTest(unittest.TestCase):
    def test_eight_unique_https_targets(self):
        labels = [t[0] for t in diag.TARGETS]
        self.assertEqual(len(labels), 8)
        self.assertEqual(len(labels), len(set(labels)))           # 중복 label 없음
        for _label, url, ctype, _sel in diag.TARGETS:
            self.assertTrue(url.startswith("https://"), url)
            self.assertIn(ctype, ("html", "json"))

    def test_urls_imported_from_crawlers(self):
        # Single Source of Truth: 크롤러 모듈 URL과 동일해야 한다.
        from app.collectors.hiring.sites.company.naver import _LIST_URL
        naver = next(t for t in diag.TARGETS if t[0] == "NAVER")
        self.assertTrue(naver[1].startswith(_LIST_URL))


class DiagnoseRequestsTest(unittest.TestCase):
    def test_saves_raw_bytes_verbatim(self):
        # EUC-KR 바이트가 디코딩 추측 없이 원본 그대로 보존되는지.
        euc_kr_bytes = b"\xb0\xa1\xb3\xaa\xb4\xd9"  # '가나다' (EUC-KR)
        fake = mock.Mock(content=euc_kr_bytes, status_code=200, encoding="EUC-KR")
        with mock.patch.object(diag.requests, "get", return_value=fake):
            r = diag.diagnose_requests("TEST", "https://x.test", "html", self._tmp())
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["bytes"], len(euc_kr_bytes))
        saved = (self._dir / "TEST_RAW.html").read_bytes()
        self.assertEqual(saved, euc_kr_bytes)            # 바이트 원본 보존

    def test_json_extension(self):
        fake = mock.Mock(content=b"{}", status_code=200, encoding="utf-8")
        with mock.patch.object(diag.requests, "get", return_value=fake):
            diag.diagnose_requests("APITEST", "https://x.test", "json", self._tmp())
        self.assertTrue((self._dir / "APITEST_RAW.json").exists())

    def test_spa_suspect_note_when_short(self):
        fake = mock.Mock(content=b"<html></html>", status_code=200, encoding="utf-8")
        with mock.patch.object(diag.requests, "get", return_value=fake):
            r = diag.diagnose_requests("SPA", "https://x.test", "html", self._tmp())
        self.assertIn("SPA 의심", r["note"])

    def test_dns_fail_recorded_not_raised(self):
        with mock.patch.object(
            diag.requests, "get",
            side_effect=requests.exceptions.ConnectionError("getaddrinfo failed"),
        ):
            r = diag.diagnose_requests("DEAD", "https://x.test", "html", self._tmp())
        self.assertEqual(r["status"], "DNS_FAIL")
        self.assertEqual(r["bytes"], 0)
        # 파일은 만들지 않는다.
        self.assertFalse((self._dir / "DEAD_RAW.html").exists())

    # ── tmp 디렉토리 헬퍼 ──
    def _tmp(self):
        import tempfile
        self._dir = pathlib.Path(tempfile.mkdtemp())
        return self._dir


if __name__ == "__main__":
    unittest.main()
