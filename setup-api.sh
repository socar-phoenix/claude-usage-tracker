#!/bin/bash
set -e

# Claude 사용량 API 트래커 — 설치/언인스톨 스크립트

CONFIG_DIR="$HOME/.config/usage-api-tracker"
PLIST_PATH="$HOME/Library/LaunchAgents/kr.socar.claude-usage-api-tracker.plist"
COLLECTOR_URL="https://github.com/socar-phoenix/claude-usage-tracker/raw/main/collector.py"

# 언인스톨
if [ "$1" = "--uninstall" ]; then
  echo "🗑  Claude 사용량 API 트래커 제거 중..."
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  rm -rf "$CONFIG_DIR"
  echo "✅ 제거 완료!"
  exit 0
fi

# 설치
TOKEN="${1:?Usage: setup.sh <TOKEN>}"

echo "🔧 Claude 사용량 API 트래커 설치 중..."

# 1. config 디렉토리 생성
mkdir -p "$CONFIG_DIR"

# 2. 토큰 저장
echo -n "$TOKEN" > "$CONFIG_DIR/token"
chmod 600 "$CONFIG_DIR/token"

# 3. collector.py 다운로드 + setup.sh 자신도 복사
curl -sL "$COLLECTOR_URL" > "$CONFIG_DIR/collector.py"
cp "$0" "$CONFIG_DIR/setup.sh" 2>/dev/null || true

# 4. cryptography 패키지 확인/설치
if ! python3 -c "import cryptography" 2>/dev/null; then
  echo "📦 cryptography 패키지 설치 중..."
  pip3 install cryptography --quiet 2>/dev/null || pip3 install cryptography --user --quiet
fi

# 5. 키체인 접근 테스트
echo ""
echo "⚠️  macOS 키체인 접근 허용이 필요합니다."
echo "   팝업이 뜨면 비밀번호 입력 후 '항상 허용'을 선택해주세요."
echo ""
python3 -c "
import subprocess
key = subprocess.check_output(['security', 'find-generic-password', '-w', '-s', 'Claude Safe Storage', '-a', 'Claude Key']).strip()
print('✅ 키체인 접근 성공')
"

# 6. 첫 실행 테스트
echo "📊 사용량 조회 테스트..."
python3 "$CONFIG_DIR/collector.py" && echo "✅ 데이터 전송 성공!" || echo "⚠️  전송 실패 — Claude Desktop에 로그인되어 있는지 확인하세요"

# 7. launchd 등록
PYTHON3_PATH="$(which python3)"
cat > "$PLIST_PATH" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>kr.socar.claude-usage-api-tracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>PYTHON3_PATH</string>
        <string>${CONFIG_DIR}/collector.py</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${CONFIG_DIR}/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${CONFIG_DIR}/stderr.log</string>
</dict>
</plist>
PLIST_EOF

launchctl load "$PLIST_PATH"

echo ""
echo "✅ 설치 완료!"
echo "📊 5분 간격으로 자동 수집됩니다."
echo "🗑  제거: bash ~/.config/usage-api-tracker/setup.sh --uninstall"
