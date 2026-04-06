#!/bin/bash
set -e

# Claude Code 사용량 트래커 — 설치/언인스톨 스크립트

CONFIG_DIR="$HOME/.config/usage-tracker"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

# 언인스톨 모드
if [ "$1" = "--uninstall" ]; then
  echo "🗑  Claude Code 사용량 트래커 제거 중..."

  # settings.json의 statusLine 원복
  if [ -f "$CLAUDE_SETTINGS" ]; then
    node -e "
      const fs = require('fs');
      const path = require('path');
      const configDir = path.join(process.env.HOME, '.config', 'usage-tracker');
      const settingsPath = path.join(process.env.HOME, '.claude', 'settings.json');
      const s = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
      const origFile = path.join(configDir, 'original_cmd');
      if (fs.existsSync(origFile)) {
        const orig = fs.readFileSync(origFile, 'utf8').trim();
        if (orig) {
          s.statusLine = { type: 'command', command: orig };
        } else {
          delete s.statusLine;
        }
      } else if (s.statusLine && s.statusLine.command && s.statusLine.command.includes('usage-tracker')) {
        delete s.statusLine;
      }
      fs.writeFileSync(settingsPath, JSON.stringify(s, null, 2));
    "
  fi

  # config 디렉토리 삭제
  rm -rf "$CONFIG_DIR"

  echo "✅ 제거 완료!"
  exit 0
fi

# 설치 모드 (curl에서 호출 시 token이 인자로 전달됨)
TOKEN="${1:?Usage: setup.sh <TOKEN> 또는 setup.sh --uninstall}"
API_URL="${2:-}"

echo "🔧 Claude Code 사용량 트래커 설치 중..."

# 1. config 디렉토리 생성
mkdir -p "$CONFIG_DIR"

# 2. 토큰/URL 저장
echo -n "$TOKEN" > "$CONFIG_DIR/token"
chmod 600 "$CONFIG_DIR/token"
if [ -n "$API_URL" ]; then
  echo -n "$API_URL" > "$CONFIG_DIR/api_url"
fi

# 3. collector.js 다운로드
if [ -n "$API_URL" ]; then
  curl -sL "${API_URL}?action=collector" > "$CONFIG_DIR/collector.js"
fi

# 4. wrapper.sh 복사 (이 스크립트와 같은 디렉토리에 있으면 복사, 아니면 생성)
cat > "$CONFIG_DIR/wrapper.sh" << 'WRAPPER_EOF'
#!/bin/bash
INPUT=$(cat)
# 수집 스크립트에 stdin 전달 (백그라운드)
echo "$INPUT" | node "$HOME/.config/usage-tracker/collector.js" 2>/dev/null &
# 기존 statusLine 커맨드가 있으면 전달
ORIGINAL_CMD_FILE="$HOME/.config/usage-tracker/original_cmd"
if [ -f "$ORIGINAL_CMD_FILE" ]; then
  ORIG=$(cat "$ORIGINAL_CMD_FILE")
  if [ -n "$ORIG" ]; then
    echo "$INPUT" | eval "$ORIG"
    exit $?
  fi
fi
WRAPPER_EOF
chmod +x "$CONFIG_DIR/wrapper.sh"

# 5. settings.json 수정
if [ ! -f "$CLAUDE_SETTINGS" ]; then
  mkdir -p "$HOME/.claude"
  echo '{}' > "$CLAUDE_SETTINGS"
fi

node -e "
  const fs = require('fs');
  const p = process.env.HOME + '/.claude/settings.json';
  const s = JSON.parse(fs.readFileSync(p, 'utf8'));
  const configDir = process.env.HOME + '/.config/usage-tracker';

  // 이미 설치되어 있으면 스킵
  if (s.statusLine && s.statusLine.command && s.statusLine.command.includes('usage-tracker')) {
    console.log('이미 설치되어 있습니다.');
    process.exit(0);
  }

  // 기존 statusLine command 백업
  if (s.statusLine && s.statusLine.command) {
    fs.writeFileSync(configDir + '/original_cmd', s.statusLine.command);
  }

  // 래퍼로 교체
  if (!s.statusLine) s.statusLine = {};
  s.statusLine.type = 'command';
  s.statusLine.command = configDir + '/wrapper.sh';

  fs.writeFileSync(p, JSON.stringify(s, null, 2));
"

echo ""
echo "✅ 설치 완료!"
echo "📊 Claude Code를 사용하면 자동으로 사용량이 추적됩니다."
