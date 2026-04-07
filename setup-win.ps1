# Claude 사용량 API 트래커 — Windows 설치/언인스톨 스크립트

param(
    [Parameter(Position=0)]
    [string]$Token,
    [switch]$Uninstall
)

$CONFIG_DIR = "$env:USERPROFILE\.config\usage-api-tracker"
$TASK_NAME = "ClaudeUsageAPITracker"
$COLLECTOR_URL = "https://github.com/socar-phoenix/claude-usage-tracker/raw/main/collector_win.py"

# 언인스톨
if ($Uninstall) {
    Write-Host ""
    Write-Host "  Claude Usage API Tracker - 제거 중..."
    Write-Host ""
    schtasks /Delete /TN $TASK_NAME /F 2>$null
    if (Test-Path $CONFIG_DIR) { Remove-Item -Recurse -Force $CONFIG_DIR }
    Write-Host "  ✓ 제거가 완료되었습니다."
    Write-Host ""
    exit 0
}

# 설치
if (-not $Token) {
    Write-Host "Usage: setup-win.ps1 <TOKEN>"
    Write-Host "       setup-win.ps1 -Uninstall"
    exit 1
}

Write-Host ""
Write-Host "╔══════════════════════════════════════╗"
Write-Host "║  Claude Usage API Tracker - 설치      ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

# 1. config 디렉토리 생성
New-Item -ItemType Directory -Force -Path $CONFIG_DIR | Out-Null

# 2. 토큰 저장
Set-Content -Path "$CONFIG_DIR\token" -Value $Token -NoNewline

# 3. collector_win.py 다운로드
Write-Host "  다운로드 중..."
Invoke-WebRequest -Uri $COLLECTOR_URL -OutFile "$CONFIG_DIR\collector_win.py"

# 4. 패키지 확인/설치
$missingPkgs = @()
python -c "import cryptography" 2>$null
if ($LASTEXITCODE -ne 0) { $missingPkgs += "cryptography" }
python -c "import win32crypt" 2>$null
if ($LASTEXITCODE -ne 0) { $missingPkgs += "pywin32" }

if ($missingPkgs.Count -gt 0) {
    Write-Host "  패키지 설치 중: $($missingPkgs -join ', ')..."
    pip install $missingPkgs --quiet 2>$null
}

# 5. 첫 실행 테스트
Write-Host "  사용량 조회 테스트..."
python "$CONFIG_DIR\collector_win.py"
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✓ 데이터 전송 성공!"
} else {
    Write-Host "  ⚠ 전송 실패 - Claude Desktop에 로그인되어 있는지 확인하세요"
}

# 6. 작업 스케줄러 등록 (5분 간격)
$pythonPath = (Get-Command python).Source
schtasks /Create /TN $TASK_NAME /TR "\"$pythonPath\" \"$CONFIG_DIR\collector_win.py\"" /SC MINUTE /MO 5 /F 2>$null

Write-Host ""
Write-Host "══════════════════════════════════════"
Write-Host "  ✓ 설치가 완료되었습니다!"
Write-Host ""
Write-Host "  5분 간격으로 자동 수집됩니다."
Write-Host ""
Write-Host "  제거: powershell -File $CONFIG_DIR\setup-win.ps1 -Uninstall"
Write-Host "══════════════════════════════════════"
Write-Host ""

# setup 스크립트 자신도 복사
Copy-Item -Path $MyInvocation.MyCommand.Path -Destination "$CONFIG_DIR\setup-win.ps1" -ErrorAction SilentlyContinue
