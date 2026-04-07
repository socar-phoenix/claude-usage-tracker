#!/usr/bin/env python3
"""
Claude 사용량 API 트래커 — claude.ai 세션 쿠키 기반 수집기
macOS Keychain에서 세션 쿠키를 추출하여 claude.ai API로 사용량 조회 후 Google Forms로 전송
"""

import hashlib
import sqlite3
import subprocess
import os
import json
import time
import sys
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
COOKIES_DB = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Claude", "Cookies")
KEYCHAIN_SERVICE = "Claude Safe Storage"
KEYCHAIN_ACCOUNT = "Claude Key"


def get_session_key():
    """macOS Keychain + Cookies DB에서 claude.ai 세션키 추출"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        print("cryptography 패키지가 필요합니다: pip3 install cryptography", file=sys.stderr)
        sys.exit(1)

    key = subprocess.check_output([
        "security", "find-generic-password", "-w",
        "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT
    ]).strip()

    aes_key = hashlib.pbkdf2_hmac("sha1", key, b"saltysalt", 1003, dklen=16)

    conn = sqlite3.connect(COOKIES_DB)
    row = conn.execute(
        "SELECT encrypted_value FROM cookies WHERE name='sessionKey' AND host_key='.claude.ai'"
    ).fetchone()
    conn.close()

    if not row:
        print("sessionKey를 찾을 수 없습니다. Claude Desktop에 로그인되어 있는지 확인하세요.", file=sys.stderr)
        sys.exit(1)

    encrypted = row[0][3:]  # v10 prefix
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b" " * 16))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    decrypted = decrypted[:-decrypted[-1]]  # PKCS7 padding
    session_key = decrypted[32:].decode("utf-8")  # DB v24: 도메인 해시

    return session_key


def fetch_usage(session_key):
    """claude.ai API에서 사용량 조회"""
    headers = {
        "Cookie": f"sessionKey={session_key}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
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
    """로컬 토큰 읽기"""
    try:
        return open(TOKEN_FILE).read().strip()
    except FileNotFoundError:
        print("토큰이 없습니다. 먼저 등록 후 setup.sh를 실행하세요.", file=sys.stderr)
        sys.exit(1)


def should_send():
    """쓰로틀링 체크"""
    try:
        last = int(open(LAST_SENT_FILE).read().strip())
        return (time.time() - last / 1000) >= THROTTLE_SEC
    except (FileNotFoundError, ValueError):
        return True


def mark_sent():
    with open(LAST_SENT_FILE, "w") as f:
        f.write(str(int(time.time() * 1000)))


def iso_to_unix(iso_str):
    """ISO 8601 → unix timestamp"""
    if not iso_str:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str)
        return str(int(dt.timestamp()))
    except Exception:
        return ""


def post_to_forms(token, usage):
    """Google Forms로 데이터 전송"""
    if not FORM_URL or not FORM_ENTRIES["token"]:
        print("Google Forms URL/entry ID가 설정되지 않았습니다.", file=sys.stderr)
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
        FORM_ENTRIES["extra_used"]: str(int(extra.get("used_credits", 0) or 0)),
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
