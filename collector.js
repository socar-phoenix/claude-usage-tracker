const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

// 5초 hard timeout
const HARD_TIMEOUT = setTimeout(() => process.exit(0), 5000);
HARD_TIMEOUT.unref();

const HOME_DIR = require('os').homedir();
const CONFIG_DIR = path.join(HOME_DIR, '.config', 'usage-tracker');
const THROTTLE_FILE = path.join(CONFIG_DIR, 'last-sent');
const QUEUE_FILE = path.join(CONFIG_DIR, 'queue.jsonl');
const UPDATE_FILE = path.join(CONFIG_DIR, '.last-update');
const THROTTLE_MS = 5 * 60 * 1000; // 5분

// ---- 설정 읽기 ----
function readConfig() {
  try {
    const token = fs.readFileSync(path.join(CONFIG_DIR, 'token'), 'utf8').trim();
    const apiUrl = fs.readFileSync(path.join(CONFIG_DIR, 'api_url'), 'utf8').trim();
    return { token, apiUrl };
  } catch {
    return null;
  }
}

// ---- 쓰로틀링 ----
function shouldSend() {
  try {
    const last = parseInt(fs.readFileSync(THROTTLE_FILE, 'utf8').trim(), 10);
    return Date.now() - last >= THROTTLE_MS;
  } catch {
    return true;
  }
}

function markSent() {
  try { fs.writeFileSync(THROTTLE_FILE, String(Date.now())); } catch {}
}

// ---- 큐 관리 ----
function enqueue(data) {
  try { fs.appendFileSync(QUEUE_FILE, data + '\n'); } catch {}
}

function drainQueue(apiUrl, token, maxItems) {
  try {
    if (!fs.existsSync(QUEUE_FILE)) return;
    const lines = fs.readFileSync(QUEUE_FILE, 'utf8').split('\n').filter(Boolean);
    if (lines.length === 0) return;
    const toSend = lines.slice(0, maxItems);
    const remaining = lines.slice(maxItems);
    let done = 0;
    const results = new Array(toSend.length).fill(false);
    for (let i = 0; i < toSend.length; i++) {
      httpPost(apiUrl, token, toSend[i], 3000, (ok) => {
        results[i] = ok;
        done++;
        if (done === toSend.length) {
          try {
            const failed = toSend.filter((_, idx) => !results[idx]);
            const kept = [...failed, ...remaining];
            if (kept.length === 0 && fs.existsSync(QUEUE_FILE)) fs.unlinkSync(QUEUE_FILE);
            else if (kept.length > 0) fs.writeFileSync(QUEUE_FILE, kept.join('\n') + '\n');
          } catch {}
        }
      });
    }
  } catch {}
}

// ---- 에러 보고 ----
const ERROR_THROTTLE_FILE = path.join(CONFIG_DIR, '.last-error');
const ERROR_THROTTLE_MS = 30 * 60 * 1000; // 30분 (에러 폭주 방지)

function shouldReportError() {
  try {
    const last = parseInt(fs.readFileSync(ERROR_THROTTLE_FILE, 'utf8').trim(), 10);
    return Date.now() - last >= ERROR_THROTTLE_MS;
  } catch { return true; }
}

function reportError(apiUrl, token, errorCode, errorMessage) {
  if (!shouldReportError()) return;
  try { fs.writeFileSync(ERROR_THROTTLE_FILE, String(Date.now())); } catch {}
  const data = JSON.stringify({
    type: 'error',
    error_code: errorCode,
    error_message: errorMessage,
  });
  enqueue(data);
  httpPost(apiUrl, token, data, 3000, (ok) => {
    if (ok) {
      try {
        if (!fs.existsSync(QUEUE_FILE)) return;
        const lines = fs.readFileSync(QUEUE_FILE, 'utf8').split('\n').filter(Boolean);
        const idx = lines.lastIndexOf(data);
        if (idx >= 0) lines.splice(idx, 1);
        if (lines.length === 0) fs.unlinkSync(QUEUE_FILE);
        else fs.writeFileSync(QUEUE_FILE, lines.join('\n') + '\n');
      } catch {}
    }
  });
}

// ---- Apps Script doPost ----
// Apps Script /exec는 302 리다이렉트를 반환하므로 수동으로 따라감
function httpPost(apiUrl, token, data, timeoutMs, callback) {
  try {
    const url = new URL(apiUrl + '?action=report');
    // HTTPS만 허용 (토큰 평문 전송 방지)
    if (url.protocol !== 'https:') { callback(false); return; }
    const mod = https;
    const parsed = typeof data === 'string' ? JSON.parse(data) : data;
    parsed.token = token;
    const body = JSON.stringify(parsed);
    const req = mod.request(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: timeoutMs,
    }, (res) => {
      // Apps Script 302 리다이렉트 처리 (POST → GET 전환, 1-hop만 지원)
      if ((res.statusCode === 301 || res.statusCode === 302) && res.headers.location) {
        res.on('data', () => {});
        res.on('end', () => {
          try {
            const locUrl = new URL(res.headers.location, url.href);
            if (locUrl.protocol !== 'https:') { callback(false); return; }
            const getReq = https.get(locUrl, { timeout: timeoutMs }, (getRes) => {
              getRes.on('data', () => {});
              getRes.on('end', () => callback(getRes.statusCode >= 200 && getRes.statusCode < 300));
            });
            getReq.on('timeout', () => { getReq.destroy(); callback(false); });
            getReq.on('error', () => callback(false));
          } catch { callback(false); }
        });
        return;
      }
      res.on('data', () => {});
      res.on('end', () => callback(res.statusCode >= 200 && res.statusCode < 300));
    });
    req.on('timeout', () => { req.destroy(); callback(false); });
    req.on('error', () => callback(false));
    req.write(body);
    req.end();
  } catch { callback(false); }
}

// ---- 셀프 업데이트 (1시간 간격) ----
const COLLECTOR_UPDATE_URL = 'https://raw.githubusercontent.com/socar-phoenix/claude-usage-tracker/main/collector.js';
const UPDATE_CHECK_MS = 60 * 60 * 1000; // 1시간

function selfUpdate() {
  try {
    // 1시간 간격 체크
    const lastCheck = fs.existsSync(UPDATE_FILE)
      ? parseInt(fs.readFileSync(UPDATE_FILE, 'utf8').trim(), 10)
      : 0;
    if (Date.now() - lastCheck < UPDATE_CHECK_MS) return;

    const url = new URL(COLLECTOR_UPDATE_URL);
    const req = https.get(url, (res) => {
      const chunks = [];
      res.on('data', (d) => chunks.push(d));
      res.on('end', () => {
        try {
          const body = Buffer.concat(chunks).toString('utf8');
          // 최소 크기 검증 (부분 수신 방지)
          if (res.statusCode !== 200 || body.length < 500) return;

          const selfPath = path.join(CONFIG_DIR, 'collector.js');
          const current = fs.existsSync(selfPath) ? fs.readFileSync(selfPath, 'utf8') : '';
          if (body.trim() === current.trim()) {
            // 변경 없음 — 체크 시간만 기록
            fs.writeFileSync(UPDATE_FILE, String(Date.now()));
            return;
          }

          // 원자적 교체: 임시 파일 → rename
          const tmpPath = selfPath + '.tmp';
          fs.writeFileSync(tmpPath, body);
          fs.renameSync(tmpPath, selfPath);
          // 성공 시에만 체크 시간 기록
          fs.writeFileSync(UPDATE_FILE, String(Date.now()));
        } catch {}
      });
    });
    req.setTimeout(3000, () => req.destroy());
    req.on('error', () => {});
  } catch {}
}

// ---- 메인 ----
let input = '';
process.stdin.on('data', (d) => input += d);
process.stdin.on('end', () => {
  try {
    const config = readConfig();
    if (!config) return;

    // statusLine 유실 감지 (FR-029)
    try {
      const settingsPath = path.join(HOME_DIR, '.claude', 'settings.json');
      const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
      const cmd = settings.statusLine && settings.statusLine.command;
      if (!cmd || !cmd.includes('usage-tracker')) {
        reportError(config.apiUrl, config.token, 'statusline_lost', 'statusLine 설정 유실');
      }
    } catch {}

    // stdin JSON 파싱
    let data;
    try {
      data = JSON.parse(input);
    } catch {
      reportError(config.apiUrl, config.token, 'parse_error', 'stdin JSON 파싱 실패');
      return;
    }

    // 셀프 업데이트 (비동기 — process.exit 전에 실행해야 콜백이 완료됨)
    selfUpdate();

    // rate_limits 확인
    const rateLimits = data.rate_limits;
    if (!rateLimits) return;

    const fiveHour = rateLimits.five_hour;
    const sevenDay = rateLimits.seven_day;
    if (!fiveHour && !sevenDay) return;

    // 쓰로틀링 체크
    if (!shouldSend()) return;

    // 전송 데이터 구성
    const payload = JSON.stringify({
      session_pct: fiveHour ? fiveHour.used_percentage : null,
      weekly_pct: sevenDay ? sevenDay.used_percentage : null,
      session_resets_at: fiveHour ? fiveHour.resets_at : null,
      weekly_resets_at: sevenDay ? sevenDay.resets_at : null,
    });

    // 전송 시도
    httpPost(config.apiUrl, config.token, payload, 3000, (ok) => {
      if (ok) markSent();
    });
  } catch (err) {
    try {
      const config = readConfig();
      if (config) reportError(config.apiUrl, config.token, 'unexpected', err.message);
    } catch {}
  }
});
