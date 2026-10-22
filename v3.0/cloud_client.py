# -*- coding: utf-8 -*-
"""
Cloud client for the v3.0 Calendar Worker.

This module has no Tkinter dependency so it can be unit-tested and reused.

Storage contract:
  guest:      ics/guest/{school}/{studentId}.ics          plaintext
  registered: ics/{school}/{apiKey}.ics                    "CAL1." + base64url(AES-GCM)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
from typing import Any, Dict, List, Optional

import requests

CAL_PREFIX = "CAL1."
DEFAULT_CLOUD_BASE = "https://calendar.thomaswan.uk"
DEFAULT_WEB_PORTAL = "https://thomaswan564.github.io/multi-tool/"

# 学校中文名 -> Worker/GitHub 使用的英文代碼
SCHOOL_CODES = {
    "私立华联大学": "hlu",
    "广州大学": "gzhu",
}


class CloudError(RuntimeError):
    """雲端操作失敗，message 可直接顯示給用戶。"""


# ---------------------------------------------------------------------------
# 加密 / URL / 二維碼
# ---------------------------------------------------------------------------


def normalize_base_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = DEFAULT_CLOUD_BASE
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


def derive_key(api_key: str) -> bytes:
    """AES-256-GCM 密鑰：SHA-256(apiKey)。與 Worker 保持一致。"""
    return hashlib.sha256(str(api_key).encode("utf-8")).digest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    clean = str(value or "").replace("-", "+").replace("_", "/")
    clean += "=" * ((4 - len(clean) % 4) % 4)
    try:
        return base64.b64decode(clean, validate=True)
    except Exception as exc:
        raise CloudError("密文格式错误") from exc


def encrypt_ics(ics_text: str, api_key: str) -> str:
    """把明文 ICS 加密成 Worker 可讀的 CAL1 密文。"""
    if not api_key:
        raise CloudError("缺少 API key，無法加密")
    if not is_ics(ics_text):
        raise CloudError("內容不是有效的 ICS 日曆")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - 依賴缺失時給出可操作提示
        raise CloudError("缺少 cryptography 依賴：請執行 pip install cryptography") from exc

    iv = os.urandom(12)
    ciphertext = AESGCM(derive_key(api_key)).encrypt(
        iv, str(ics_text).encode("utf-8"), None
    )
    return CAL_PREFIX + _b64url_encode(iv + ciphertext)


def decrypt_ics(payload: str, api_key: str) -> str:
    """解密 CAL1 密文；若輸入本身是明文 ICS，原樣返回。"""
    raw = str(payload or "")
    if not raw.strip().startswith(CAL_PREFIX):
        return raw

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover
        raise CloudError("缺少 cryptography 依賴：請執行 pip install cryptography") from exc

    data = _b64url_decode(raw.strip()[len(CAL_PREFIX) :])
    if len(data) < 12 + 16:
        raise CloudError("密文長度不足")
    iv, ciphertext = data[:12], data[12:]
    try:
        return AESGCM(derive_key(api_key)).decrypt(iv, ciphertext, None).decode("utf-8")
    except Exception as exc:
        raise CloudError("解密失敗：API key 不正確或文件已損壞") from exc


def is_ics(text: str) -> bool:
    value = str(text or "")
    return "BEGIN:VCALENDAR" in value and "END:VCALENDAR" in value


def webcal_url(url: str) -> str:
    return re.sub(r"^https?://", "webcal://", str(url or ""), flags=re.IGNORECASE)


def subscription_url(
    base_url: str,
    school: str,
    api_key: Optional[str] = None,
    student_id: Optional[str] = None,
) -> str:
    base = normalize_base_url(base_url)
    school = str(school or "").strip().lower()
    if api_key:
        return f"{base}/{school}/{api_key}.ics"
    if student_id:
        return f"{base}/guest/{school}/{student_id}.ics"
    raise CloudError("缺少 API key 或學號，無法生成訂閱地址")


def caldav_url(base_url: str, school: str, api_key: str) -> str:
    base = normalize_base_url(base_url)
    return f"{base}/caldav/{str(school).strip().lower()}/{api_key}/"


def make_qr_png(data: str, box_size: int = 6, border: int = 2) -> bytes:
    """生成二維碼 PNG 字節。需要 qrcode（Pillow 可選）。"""
    try:
        import qrcode
    except ImportError as exc:
        raise CloudError(
            "缺少 qrcode 依賴：請執行 pip install qrcode[pil]，"
            "或把 qrcode 放入程序目錄的 _vendor 文件夾"
        ) from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(2, int(box_size)),
        border=max(0, int(border)),
    )
    qr.add_data(str(data))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def school_code(school_name: str) -> str:
    """優先使用內置映射；未知學校退化為安全的小寫代碼。"""
    name = str(school_name or "").strip()
    if name in SCHOOL_CODES:
        return SCHOOL_CODES[name]
    code = re.sub(r"[^a-zA-Z0-9_-]+", "", name).lower()
    return code[:20] or "school"


def load_cloud_config(app_dir: str) -> Dict[str, Any]:
    """讀取可選的 cloud_config.json，缺省值可正常運行。"""
    config: Dict[str, Any] = {
        "cloud_base_url": DEFAULT_CLOUD_BASE,
        "web_portal_url": DEFAULT_WEB_PORTAL,
        "github_owner": "thomaswan564",
        "github_repo": "multi-tool",
        "update_check": True,
    }
    path = os.path.join(str(app_dir or "."), "cloud_config.json")
    if not os.path.isfile(path):
        return config
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            for key in ("cloud_base_url", "web_portal_url", "github_owner", "github_repo"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    if key == "web_portal_url" and any(
                        placeholder in value.upper()
                        for placeholder in ("YOURNAME", "YOUR_REPO")
                    ):
                        continue
                    config[key] = value.strip()
            value = data.get("update_check")
            if isinstance(value, bool):
                config["update_check"] = value
            elif isinstance(value, str):
                config["update_check"] = value.strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        pass
    return config


def save_cloud_config(
    app_dir: str,
    cloud_base_url: Optional[str] = None,
    web_portal_url: Optional[str] = None,
    github_owner: Optional[str] = None,
    github_repo: Optional[str] = None,
    update_check: Optional[bool] = None,
) -> None:
    """保存配置；未傳入的欄位沿用現有 cloud_config.json 的值。"""
    config = load_cloud_config(app_dir)
    if cloud_base_url is not None:
        config["cloud_base_url"] = normalize_base_url(cloud_base_url)
    if web_portal_url is not None:
        config["web_portal_url"] = str(web_portal_url or "").strip()
    if github_owner is not None:
        config["github_owner"] = str(github_owner or "").strip()
    if github_repo is not None:
        config["github_repo"] = str(github_repo or "").strip()
    if update_check is not None:
        config["update_check"] = bool(update_check)

    path = os.path.join(str(app_dir or "."), "cloud_config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def resolve_update_source(config: Dict[str, Any]) -> tuple:
    """解析 GitHub Releases 的 owner/repo。

    優先使用 github_owner/github_repo；否則從 GitHub Pages 地址
    https://<owner>.github.io/<repo>/ 推斷。佔位符視為未配置。
    """
    owner = str((config or {}).get("github_owner") or "").strip()
    repo = str((config or {}).get("github_repo") or "").strip()
    if owner and repo:
        if "YOURNAME" not in owner.upper() and "YOUR_REPO" not in repo.upper():
            return owner, repo

    portal = str((config or {}).get("web_portal_url") or "")
    if "YOURNAME" in portal.upper() or "YOUR_REPO" in portal.upper():
        return "", ""

    match = re.match(
        r"https?://([^./]+)\.github\.io/([^/?#]+)",
        portal,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1), match.group(2)
    return "", ""


def parse_version(value: str) -> tuple:
    """從 tag/版本字串提取數字元組，例如 v3.0.1 -> (3, 0, 1)。"""
    match = re.search(r"(\d+(?:\.\d+)*)", str(value or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(latest: str, current: str) -> bool:
    """比較兩個版本號；缺少的段補 0。"""
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    if not latest_parts:
        return False
    size = max(len(latest_parts), len(current_parts))
    latest_padded = latest_parts + (0,) * (size - len(latest_parts))
    current_padded = current_parts + (0,) * (size - len(current_parts))
    return latest_padded > current_padded


def fetch_latest_release(owner: str, repo: str, timeout: int = 12) -> Dict[str, Any]:
    """查詢 GitHub Releases 最新正式版本（不需要登入，公開倉庫即可）。"""
    owner = str(owner or "").strip()
    repo = str(repo or "").strip()
    if not owner or not repo:
        raise CloudError("未配置 GitHub owner/repo，無法檢測版本")

    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "calendar-desktop-update-check",
    }

    try:
        response = requests.get(url, headers=headers, timeout=int(timeout))
    except requests.RequestException as exc:
        raise CloudError(f"版本檢測失敗：{exc}") from exc

    if response.status_code == 404:
        raise CloudError(f"倉庫 {owner}/{repo} 沒有 Release 或不存在")
    if response.status_code != 200:
        raise CloudError(f"版本檢測失敗（HTTP {response.status_code}）")

    try:
        data = response.json()
    except ValueError as exc:
        raise CloudError("版本檢測返回非 JSON 數據") from exc

    if not isinstance(data, dict) or not data.get("tag_name"):
        raise CloudError("Release 缺少 tag_name")

    download_url = ""
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "").lower().endswith(".exe"):
            download_url = str(asset.get("browser_download_url") or "")
            if download_url:
                break

    return {
        "tag": str(data.get("tag_name") or ""),
        "name": str(data.get("name") or ""),
        "html_url": str(data.get("html_url") or ""),
        "download_url": download_url or str(data.get("html_url") or ""),
        "published_at": str(data.get("published_at") or ""),
        "body": str(data.get("body") or ""),
    }


# ---------------------------------------------------------------------------
# Worker API client
# ---------------------------------------------------------------------------


class CloudAPI:
    """Worker JSON API 的輕量封裝。"""

    def __init__(self, base_url: str = DEFAULT_CLOUD_BASE, timeout: int = 45):
        self.base_url = normalize_base_url(base_url)
        self.timeout = int(timeout)
        self.session = requests.Session()

    # -- low level ---------------------------------------------------------

    def _url(self, path: str) -> str:
        return self.base_url + "/" + str(path).lstrip("/")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            return self.session.request(
                method, self._url(path), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise CloudError(f"無法連接雲端（{self.base_url}）：{exc}") from exc

    def _json(self, response: requests.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            text = (response.text or "").strip().replace("\n", " ")[:160]
            raise CloudError(
                f"雲端返回異常（HTTP {response.status_code}）：{text or '非 JSON 響應'}"
            ) from exc

        if not isinstance(payload, dict):
            raise CloudError("雲端返回格式錯誤")

        if response.status_code >= 400 or payload.get("success") is False:
            raise CloudError(str(payload.get("error") or f"HTTP {response.status_code}"))
        return payload

    # -- auth --------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return self._json(self._request("GET", "/health"))

    def captcha(self) -> Dict[str, Any]:
        return self._json(self._request("GET", "/api/captcha"))

    def login(self, username: str, password: str) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/api/login",
            json={"username": str(username), "password": str(password)},
        )
        return self._json(response)

    def register(
        self,
        username: str,
        password: str,
        school: str,
        captcha_token: str = "",
        captcha_answer: str = "",
        turnstile_token: str = "",
    ) -> Dict[str, Any]:
        body = {
            "username": str(username),
            "password": str(password),
            "school": str(school),
            "captchaToken": str(captcha_token),
            "captchaAnswer": str(captcha_answer),
            "cf-turnstile-response": str(turnstile_token),
        }
        return self._json(self._request("POST", "/api/register", json=body))

    def me(self, api_key: str) -> Dict[str, Any]:
        response = self._request(
            "GET", "/api/me", headers={"Authorization": f"Bearer {api_key}"}
        )
        return self._json(response)

    # -- registered --------------------------------------------------------

    def list_registered(self, api_key: str) -> Dict[str, Any]:
        response = self._request(
            "GET", "/api/files", headers={"Authorization": f"Bearer {api_key}"}
        )
        return self._json(response)

    def upload_registered(
        self, api_key: str, ics_text: str, filename: Optional[str] = None
    ) -> Dict[str, Any]:
        if not api_key:
            raise CloudError("請填寫 API key")
        encrypted = encrypt_ics(ics_text, api_key)
        files = {
            "file": (
                filename or f"{api_key}.ics",
                encrypted.encode("utf-8"),
                "text/plain",
            )
        }
        response = self._request(
            "POST",
            "/api/upload",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
        )
        return self._json(response)

    def delete_registered(self, api_key: str, filename: str) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/api/files/delete",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"filename": str(filename)},
        )
        return self._json(response)

    # -- guest -------------------------------------------------------------

    def list_guest(self, school: str, student_id: str) -> Dict[str, Any]:
        response = self._request(
            "GET",
            "/api/guest/files",
            params={"school": str(school), "studentId": str(student_id)},
        )
        return self._json(response)

    def upload_guest(self, school: str, student_id: str, ics_text: str) -> Dict[str, Any]:
        if not school or not student_id:
            raise CloudError("訪客上傳需要學校與學號")
        files = {
            "file": (
                f"{student_id}.ics",
                str(ics_text).encode("utf-8"),
                "text/calendar",
            )
        }
        response = self._request(
            "POST",
            "/api/guest/upload",
            data={"school": str(school), "studentId": str(student_id)},
            files=files,
        )
        return self._json(response)

    def delete_guest(self, school: str, student_id: str) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/api/guest/delete",
            json={"school": str(school), "studentId": str(student_id)},
        )
        return self._json(response)

    # -- account -----------------------------------------------------------

    def change_password(self, api_key: str, old_password: str, new_password: str) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/api/account/password",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"oldPassword": str(old_password), "newPassword": str(new_password)},
        )
        return self._json(response)

    def delete_account(self, api_key: str) -> Dict[str, Any]:
        response = self._request(
            "POST",
            "/api/account/delete",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        return self._json(response)
