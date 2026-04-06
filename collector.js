const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

// 5초 hard timeout
const HARD_TIMEOUT = setTimeout(() => process.exit(0), 5000);
HARD_TIMEOUT.unref();

const CONFIG_DIR = path.join(require('os').homedir(), '.config', 'usage-tracker');
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

// ---- HTTP POST ----
function httpPost(apiUrl, token, data, timeoutMs, callback) {
  try {
    const url = new URL(apiUrl + '?action=report&auth=' + encodeURIComponent(token));
    const mod = url.protocol === 'https:' ? https : http;
    const req = mod.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      timeout: timeoutMs,
    }, (res) => {
      res.on('data', () => {});
      res.on('end', () => callback(res.statusCode >= 200 && res.statusCode < 300));
    });
    req.on('timeout', () => { req.destroy(); callback(false); });
    req.on('error', () => callback(false));
    req.write(data);
    req.end();
  } catch { callback(false); }
}

// ---- 셀프 업데이트 (하루 1회) (T016) ----
function selfUpdate(apiUrl) {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const lastUpdate = fs.existsSync(UPDATE_FILE) ? fs.readFileSync(UPDATE_FILE, 'utf8').trim() : '';
    if (lastUpdate === today) return;
    const url = new URL(apiUrl + '?action=collector');
    const mod = url.protocol === 'https:' ? https : http;
    const req = mod.get(url, (res) => {
      let body = '';
      res.on('data', (d) => body += d);
      res.on('end', () => {
        try {
          if (res.statusCode === 200 && body.length > 100) {
            const selfPath = path.join(CONFIG_DIR, 'collector.js');
            const current = fs.existsSync(selfPath) ? fs.readFileSync(selfPath, 'utf8') : '';
            if (body.trim() !== current.trim()) fs.writeFileSync(selfPath, body);
          }
          fs.writeFileSync(UPDATE_FILE, today);
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
    if (!config) process.exit(0);

    // stdin JSON 파싱
    const data = JSON.parse(input);

    // rate_limits 확인
    const rateLimits = data.rate_limits;
    if (!rateLimits) process.exit(0);

    const fiveHour = rateLimits.five_hour;
    const sevenDay = rateLimits.seven_day;
    if (!fiveHour && !sevenDay) process.exit(0);

    // 셀프 업데이트
    selfUpdate(config.apiUrl);

    // 큐 drain
    drainQueue(config.apiUrl, config.token, 5);

    // 쓰로틀링 체크
    if (!shouldSend()) process.exit(0);

    // 전송 데이터 구성
    const payload = JSON.stringify({
      session_pct: fiveHour ? fiveHour.used_percentage : null,
      weekly_pct: sevenDay ? sevenDay.used_percentage : null,
      session_resets_at: fiveHour ? fiveHour.resets_at : null,
      weekly_resets_at: sevenDay ? sevenDay.resets_at : null,
    });

    // 큐에 추가
    enqueue(payload);

    // 전송 시도
    httpPost(config.apiUrl, config.token, payload, 3000, (ok) => {
      if (ok) {
        markSent();
        // 큐에서 방금 넣은 항목 제거
        try {
          const qLines = fs.readFileSync(QUEUE_FILE, 'utf8').split('\n').filter(Boolean);
          const idx = qLines.lastIndexOf(payload);
          if (idx >= 0) qLines.splice(idx, 1);
          if (qLines.length === 0 && fs.existsSync(QUEUE_FILE)) fs.unlinkSync(QUEUE_FILE);
          else if (qLines.length > 0) fs.writeFileSync(QUEUE_FILE, qLines.join('\n') + '\n');
        } catch {}
      }
      process.exit(0);
    });
  } catch {
    process.exit(0);
  }
});
