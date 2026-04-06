#!/usr/bin/env python3
"""
Claude 사용량 수집기 — claude.ai API 기반
Desktop 세션 쿠키를 이용하여 사용량을 조회하고 Google Forms로 전송
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
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf_YBvK5o-YvrQAuillswvnnyjf96YVkmkU9D5B5GrQ2X7k2Q/formResponse"
FORM_ENTRIES = {
    "token": "entry.2039460777",
    "session_pct": "entry.236579146",
    "weekly_pct": "entry.1074971121",
    "session_resets_at": "entry.1779805045",
    "weekly_resets_at": "entry.1545380631",
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

    # Keychain에서 암호화 키
    key = subprocess.check_output([
        "security", "find-generic-password", "-w",
        "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT
    ]).strip()

    # AES 키 유도
    aes_key = hashlib.pbkdf2_hmac("sha1", key, b"saltysalt", 1003, dklen=16)

    # Cookies DB에서 sessionKey 읽기
    conn = sqlite3.connect(COOKIES_DB)
    row = conn.execute(
        "SELECT encrypted_value FROM cookies WHERE name='sessionKey' AND host_key='.claude.ai'"
    ).fetchone()
    conn.close()

    if not row:
        print("sessionKey를 찾을 수 없습니다. Claude Desktop에 로그인되어 있는지 확인하세요.", file=sys.stderr)
        sys.exit(1)

    # v10 복호화
    encrypted = row[0][3:]  # v10 prefix 제거
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b" " * 16))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    decrypted = decrypted[:-decrypted[-1]]  # PKCS7 padding 제거
    session_key = decrypted[32:].decode("utf-8")  # DB v24: 도메인 해시 제거

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

    req = urllib.request.Request(
        f"https://claude.ai/api/organizations/{ORG_UUID}/usage"
    )
    for k, v in headers.items():
        req.add_header(k, v)

    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())


def read_token():
    """로컬에 저장된 사용자 토큰 읽기"""
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
    """전송 시점 기록"""
    with open(LAST_SENT_FILE, "w") as f:
        f.write(str(int(time.time() * 1000)))


def post_to_forms(token, usage):
    """Google Forms로 데이터 전송"""
    five_hour = usage.get("five_hour") or {}
    seven_day = usage.get("seven_day") or {}

    session_pct = five_hour.get("utilization", 0) or 0
    weekly_pct = seven_day.get("utilization", 0) or 0

    # resets_at을 unix timestamp로 변환
    session_resets = five_hour.get("resets_at", "")
    weekly_resets = seven_day.get("resets_at", "")

    def iso_to_unix(iso_str):
        if not iso_str:
            return ""
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(iso_str.replace("+00:00", "+00:00"))
            return str(int(dt.timestamp()))
        except:
            return ""

    data = urllib.parse.urlencode({
        FORM_ENTRIES["token"]: token,
        FORM_ENTRIES["session_pct"]: str(int(session_pct)),
        FORM_ENTRIES["weekly_pct"]: str(int(weekly_pct)),
        FORM_ENTRIES["session_resets_at"]: iso_to_unix(session_resets),
        FORM_ENTRIES["weekly_resets_at"]: iso_to_unix(weekly_resets),
    }).encode()

    req = urllib.request.Request(FORM_URL, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status < 400
    except:
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
