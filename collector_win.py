#!/usr/bin/env python3
"""
Claude 사용량 API 트래커 — Windows용 수집기
Claude Desktop 쿠키(DPAPI)를 복호화하여 claude.ai API로 사용량 조회 후 Google Forms로 전송
"""

import hashlib
import sqlite3
import os
import json
import time
import sys
import base64
import shutil
import tempfile
import urllib.request
import urllib.parse
import urllib.error

# ---- 설정 ----
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "usage-api-tracker")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")
LAST_SENT_FILE = os.path.join(CONFIG_DIR, "last-sent")
THROTTLE_SEC = 300  # 5분

# Google Forms
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf8o2_qXcTOrTtZQQKzbTxoaOFcglNktWU10DJ9ZOLlocmHYg/formResponse"
FORM_ENTRIES = {
    "token": "entry.1687704276",
    "session_pct": "entry.54462484",
    "weekly_pct": "entry.1813634161",
    "weekly_sonnet_pct": "entry.1921007693",
    "extra_used": "entry.796315702",
    "session_resets_at": "entry.185492805",
    "weekly_resets_at": "entry.1159006916",
}

# claude.ai
ORG_UUID = "8e9d59b5-d036-448c-a64b-6c83250c6091"

# Windows 경로
APPDATA = os.environ.get("LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Local"))
COOKIES_DB = os.path.join(APPDATA, "Claude", "Cookies")
LOCAL_STATE = os.path.join(APPDATA, "Claude", "Local State")


def get_session_key():
    """Windows DPAPI + Cookies DB에서 claude.ai 세션키 추출"""
    try:
        import win32crypt
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        print("필요한 패키지: pip install pywin32 cryptography", file=sys.stderr)
        sys.exit(1)

    # Local State에서 암호화 키 추출
    with open(LOCAL_STATE, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
    encrypted_key = base64.b64decode(encrypted_key_b64)
    # "DPAPI" prefix (5 bytes) 제거 후 DPAPI 복호화
    encrypted_key = encrypted_key[5:]
    decrypted_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]

    # Cookies DB 복사 (잠금 방지)
    tmp_db = os.path.join(tempfile.gettempdir(), "claude_cookies_tmp.db")
    shutil.copy2(COOKIES_DB, tmp_db)

    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT encrypted_value FROM cookies WHERE name='sessionKey' AND host_key='.claude.ai'"
    ).fetchone()
    conn.close()
    os.remove(tmp_db)

    if not row:
        print("sessionKey를 찾을 수 없습니다. Claude Desktop에 로그인되어 있는지 확인하세요.", file=sys.stderr)
        sys.exit(1)

    encrypted_value = row[0]

    # v10 = AES-128-CBC (macOS), v20 = AES-256-GCM (Windows)
    prefix = encrypted_value[:3].decode("ascii", errors="replace")

    if prefix == "v20" or prefix == "v10":
        # Windows: v10/v20 → AES-256-GCM
        nonce = encrypted_value[3:15]  # 12 bytes
        ciphertext = encrypted_value[15:]
        aesgcm = AESGCM(decrypted_key)
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        session_key = decrypted.decode("utf-8")
    else:
        # 구형 DPAPI 직접 암호화
        session_key = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode("utf-8")

    return session_key


def fetch_usage(session_key):
    """claude.ai API에서 사용량 조회"""
    headers = {
        "Cookie": f"sessionKey={session_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Origin": "https://claude.ai",
        "Referer": "https://claude.ai/settings/usage",
    }

    req = urllib.request.Request(f"https://claude.ai/api/organizations/{ORG_UUID}/usage")
    for k, v in headers.items():
        req.add_header(k, v)

    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())


def read_token():
    try:
        return open(TOKEN_FILE).read().strip()
    except FileNotFoundError:
        print("토큰이 없습니다. 먼저 등록 후 설치 스크립트를 실행하세요.", file=sys.stderr)
        sys.exit(1)


def should_send():
    try:
        last = int(open(LAST_SENT_FILE).read().strip())
        return (time.time() - last / 1000) >= THROTTLE_SEC
    except (FileNotFoundError, ValueError):
        return True


def mark_sent():
    with open(LAST_SENT_FILE, "w") as f:
        f.write(str(int(time.time() * 1000)))


def iso_to_unix(iso_str):
    if not iso_str:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_str)
        return str(int(dt.timestamp()))
    except Exception:
        return ""


def post_to_forms(token, usage):
    if not FORM_URL or not FORM_ENTRIES["token"]:
        return False

    five_hour = usage.get("five_hour") or {}
    seven_day = usage.get("seven_day") or {}
    seven_day_sonnet = usage.get("seven_day_sonnet") or {}
    extra = usage.get("extra_usage") or {}

    data = urllib.parse.urlencode({
        FORM_ENTRIES["token"]: token,
        FORM_ENTRIES["session_pct"]: str(int(five_hour.get("utilization", 0) or 0)),
        FORM_ENTRIES["weekly_pct"]: str(int(seven_day.get("utilization", 0) or 0)),
        FORM_ENTRIES["weekly_sonnet_pct"]: str(int(seven_day_sonnet.get("utilization", 0) or 0)),
        FORM_ENTRIES["extra_used"]: str(round((extra.get("used_credits", 0) or 0) / 100, 2)),
        FORM_ENTRIES["session_resets_at"]: iso_to_unix(five_hour.get("resets_at", "")),
        FORM_ENTRIES["weekly_resets_at"]: iso_to_unix(seven_day.get("resets_at", "")),
    }).encode()

    req = urllib.request.Request(FORM_URL, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status < 400
    except Exception:
        return False


def main():
    if not should_send():
        return

    token = read_token()

    try:
        session_key = get_session_key()
    except Exception as e:
        print(f"세션키 추출 실패: {e}", file=sys.stderr)
        return

    try:
        usage = fetch_usage(session_key)
    except Exception as e:
        print(f"사용량 조회 실패: {e}", file=sys.stderr)
        return

    if post_to_forms(token, usage):
        mark_sent()


if __name__ == "__main__":
    main()
