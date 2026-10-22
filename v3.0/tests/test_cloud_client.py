# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_vendor"))

from cloud_client import (  # noqa: E402
    CAL_PREFIX,
    CloudAPI,
    CloudError,
    caldav_url,
    decrypt_ics,
    encrypt_ics,
    fetch_latest_release,
    is_newer_version,
    load_cloud_config,
    make_qr_png,
    parse_version,
    resolve_update_source,
    save_cloud_config,
    subscription_url,
    webcal_url,
)

ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:test-1\r\n"
    "SUMMARY:Test Course\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

API_KEY = "a" * 48


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class EncryptionTests(unittest.TestCase):
    def test_roundtrip(self):
        payload = encrypt_ics(ICS, API_KEY)
        self.assertTrue(payload.startswith(CAL_PREFIX))
        self.assertNotIn("BEGIN:VCALENDAR", payload)
        self.assertEqual(decrypt_ics(payload, API_KEY), ICS)

    def test_random_iv(self):
        self.assertNotEqual(encrypt_ics(ICS, API_KEY), encrypt_ics(ICS, API_KEY))

    def test_wrong_key(self):
        payload = encrypt_ics(ICS, API_KEY)
        with self.assertRaises(CloudError):
            decrypt_ics(payload, "b" * 48)

    def test_plaintext_passthrough(self):
        self.assertEqual(decrypt_ics(ICS, API_KEY), ICS)

    def test_reject_non_ics(self):
        with self.assertRaises(CloudError):
            encrypt_ics("hello", API_KEY)


class UrlTests(unittest.TestCase):
    def test_registered_urls(self):
        self.assertEqual(
            subscription_url("https://calendar.test/", "gzhu", API_KEY),
            f"https://calendar.test/gzhu/{API_KEY}.ics",
        )
        self.assertEqual(
            caldav_url("calendar.test", "gzhu", API_KEY),
            f"https://calendar.test/caldav/gzhu/{API_KEY}/",
        )
        self.assertEqual(
            webcal_url(f"https://calendar.test/gzhu/{API_KEY}.ics"),
            f"webcal://calendar.test/gzhu/{API_KEY}.ics",
        )

    def test_guest_url(self):
        self.assertEqual(
            subscription_url("https://calendar.test", "hlu", student_id="2023001"),
            "https://calendar.test/guest/hlu/2023001.ics",
        )

    def test_missing_key(self):
        with self.assertRaises(CloudError):
            subscription_url("https://calendar.test", "gzhu")


class QrTests(unittest.TestCase):
    def test_qr_png(self):
        data = make_qr_png("webcal://calendar.test/gzhu/abc.ics")
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(data), 200)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_override(self):
        folder = os.path.join(HERE, "_tmp_config")
        os.makedirs(folder, exist_ok=True)
        try:
            config = load_cloud_config(folder)
            self.assertEqual(config["cloud_base_url"], "https://calendar.thomaswan.uk")
            self.assertEqual(config["web_portal_url"], "https://thomaswan564.github.io/multi-tool/")
            self.assertEqual(config["github_owner"], "thomaswan564")
            self.assertEqual(config["github_repo"], "multi-tool")
            self.assertTrue(config["update_check"])

            save_cloud_config(
                folder,
                "https://example.test/",
                "https://user.github.io/calendar/",
                github_owner="user",
                github_repo="calendar",
                update_check=False,
            )
            config = load_cloud_config(folder)
            self.assertEqual(config["cloud_base_url"], "https://example.test")
            self.assertEqual(config["web_portal_url"], "https://user.github.io/calendar/")
            self.assertEqual(config["github_owner"], "user")
            self.assertEqual(config["github_repo"], "calendar")
            self.assertFalse(config["update_check"])
        finally:
            try:
                os.remove(os.path.join(folder, "cloud_config.json"))
                os.rmdir(folder)
            except OSError:
                pass


class UpdateTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("v3.0.1"), (3, 0, 1))
        self.assertEqual(parse_version("Releases-v1.1"), (1, 1))
        self.assertEqual(parse_version("no-version"), ())

    def test_version_compare(self):
        self.assertTrue(is_newer_version("v3.0.1", "3.0.0"))
        self.assertFalse(is_newer_version("v3.0.0", "3.0"))
        self.assertFalse(is_newer_version("Releases-v1.1", "3.0.0"))
        self.assertTrue(is_newer_version("4.0", "3.9.9"))

    def test_resolve_update_source(self):
        owner, repo = resolve_update_source(
            {"github_owner": "thomaswan", "github_repo": "multi-tool"}
        )
        self.assertEqual((owner, repo), ("thomaswan", "multi-tool"))

        owner, repo = resolve_update_source(
            {"web_portal_url": "https://alice.github.io/calendar/"}
        )
        self.assertEqual((owner, repo), ("alice", "calendar"))

        owner, repo = resolve_update_source({})
        self.assertEqual((owner, repo), ("", ""))

        owner, repo = resolve_update_source(
            {"web_portal_url": "https://YOURNAME.github.io/YOUR_REPO/"}
        )
        self.assertEqual((owner, repo), ("", ""))

    def test_fetch_latest_release(self):
        payload = {
            "tag_name": "v3.0.1",
            "name": "Calendar 3.0.1",
            "html_url": "https://github.com/alice/calendar/releases/tag/v3.0.1",
            "assets": [
                {
                    "name": "Calendar-v3.0.1.exe",
                    "browser_download_url": "https://github.com/alice/calendar/releases/download/v3.0.1/Calendar.exe",
                }
            ],
        }
        with patch.object(requests, "get", return_value=FakeResponse(payload)) as get:
            release = fetch_latest_release("alice", "calendar")
        self.assertEqual(release["tag"], "v3.0.1")
        self.assertTrue(release["download_url"].endswith("Calendar.exe"))
        self.assertIn("/releases/tag/v3.0.1", release["html_url"])
        called_url = get.call_args[0][0]
        self.assertEqual(called_url, "https://api.github.com/repos/alice/calendar/releases/latest")
        self.assertIn("application/vnd.github+json", get.call_args[1]["headers"]["Accept"])

    def test_fetch_latest_release_missing(self):
        with patch.object(requests, "get", return_value=FakeResponse({"message": "Not Found"}, 404)):
            with self.assertRaisesRegex(CloudError, "沒有 Release"):
                fetch_latest_release("alice", "calendar")


class ApiClientTests(unittest.TestCase):
    def test_auth_guest_and_account_endpoints(self):
        calls = []

        def fake_request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse({"success": True})

        with patch.object(requests.Session, "request", fake_request):
            api = CloudAPI("https://calendar.test")
            api.health()
            api.captcha()
            api.login("alice", "password")
            api.register("alice", "password", "hlu", "ct", "42", "turnstile")
            api.me(API_KEY)
            api.list_registered(API_KEY)
            api.delete_registered(API_KEY, "x.ics")
            api.list_guest("hlu", "2023001")
            api.delete_guest("hlu", "2023001")
            api.change_password(API_KEY, "old", "new")
            api.delete_account(API_KEY)

        self.assertEqual([call[1].removeprefix("https://calendar.test") for call in calls], [
            "/health", "/api/captcha", "/api/login", "/api/register", "/api/me",
            "/api/files", "/api/files/delete", "/api/guest/files",
            "/api/guest/delete", "/api/account/password", "/api/account/delete",
        ])
        self.assertEqual(calls[2][2]["json"], {"username": "alice", "password": "password"})
        self.assertEqual(calls[3][2]["json"]["school"], "hlu")
        self.assertEqual(calls[7][2]["params"], {"school": "hlu", "studentId": "2023001"})

    def test_upload_guest_rejects_missing_identity(self):
        with self.assertRaisesRegex(CloudError, "訪客上傳需要學校與學號"):
            CloudAPI("https://calendar.test").upload_guest("", "", ICS)

    def test_upload_registered_encrypts_and_sends_bearer(self):
        calls = []

        def fake_request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse({"success": True, "url": "https://calendar.test/gzhu/x.ics"})

        with patch.object(requests.Session, "request", fake_request):
            api = CloudAPI("https://calendar.test")
            result = api.upload_registered(API_KEY, ICS)

        self.assertTrue(result["success"])
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("/api/upload"))
        kwargs = calls[0][2]
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {API_KEY}")
        payload = kwargs["files"]["file"][1].decode("utf-8")
        self.assertTrue(payload.startswith(CAL_PREFIX))
        self.assertEqual(decrypt_ics(payload, API_KEY), ICS)

    def test_upload_guest_plaintext(self):
        calls = []

        def fake_request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse({"success": True, "url": "https://calendar.test/guest/gzhu/1.ics"})

        with patch.object(requests.Session, "request", fake_request):
            api = CloudAPI("https://calendar.test")
            api.upload_guest("gzhu", "2023001", ICS)

        kwargs = calls[0][2]
        self.assertEqual(kwargs["data"], {"school": "gzhu", "studentId": "2023001"})
        payload = kwargs["files"]["file"][1].decode("utf-8")
        self.assertEqual(payload, ICS)

    def test_error_payload_raises(self):
        def fake_request(self, method, url, **kwargs):
            return FakeResponse({"success": False, "error": "bad key"}, 401)

        with patch.object(requests.Session, "request", fake_request):
            api = CloudAPI("https://calendar.test")
            with self.assertRaisesRegex(CloudError, "bad key"):
                api.me(API_KEY)


if __name__ == "__main__":
    unittest.main()
