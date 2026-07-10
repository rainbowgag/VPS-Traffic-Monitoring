#!/usr/bin/env python3
import argparse
import calendar
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_NAME = "vps-traffic-monitor"
DEFAULT_CONFIG = "/etc/vps-traffic-monitor/config.json"
DEFAULT_DB = "/var/lib/vps-traffic-monitor/traffic.db"
DEFAULT_PORT = 8088
DEFAULT_INTERVAL = 5
MAX_REASONABLE_DELTA = 1024 ** 5


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VPS Traffic Monitoring</title>
  <style>
    :root { color-scheme: dark light; --bg:#10141d; --panel:#171d29; --text:#edf2f7; --muted:#9fb0ca; --line:#2b3445; --accent:#34b6b8; --warn:#e0714a; --good:#55c796; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    header { padding:24px clamp(16px,4vw,42px) 14px; border-bottom:1px solid var(--line); background:var(--panel); }
    .top { display:flex; gap:16px; align-items:flex-end; justify-content:space-between; max-width:1180px; margin:0 auto; }
    h1 { margin:0; font-size:clamp(26px,4vw,44px); line-height:1.1; letter-spacing:0; }
    h2 { margin:0 0 14px; font-size:20px; letter-spacing:0; }
    .sub,.hint,.label { color:var(--muted); }
    .actions { display:flex; gap:10px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
    .status { display:inline-flex; align-items:center; gap:8px; min-height:34px; padding:6px 10px; border:1px solid var(--line); border-radius:8px; color:var(--muted); white-space:nowrap; }
    .dot { width:9px; height:9px; border-radius:50%; background:var(--good); box-shadow:0 0 0 4px rgba(85,199,150,.18); }
    main { width:min(1180px, calc(100% - 32px)); margin:22px auto 40px; }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; }
    .metric { padding:18px; min-height:136px; }
    .value { margin-top:8px; font-size:clamp(30px,4vw,44px); font-weight:760; line-height:1; letter-spacing:0; overflow-wrap:anywhere; }
    .section { margin-top:14px; padding:18px; }
    .span2 { grid-column:span 2; }
    .locked,.hidden { display:none !important; }
    table { width:100%; border-collapse:collapse; }
    th,td { padding:11px 8px; border-bottom:1px solid var(--line); text-align:left; }
    th { color:var(--muted); font-weight:650; }
    .toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:end; }
    label { display:grid; gap:6px; color:var(--muted); min-width:120px; }
    input,select,button { height:38px; border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--text); padding:0 10px; font:inherit; }
    button { cursor:pointer; background:var(--accent); color:#fff; border-color:transparent; font-weight:700; }
    button.secondary { background:transparent; color:var(--text); border-color:var(--line); }
    .message { color:var(--warn); margin-top:10px; min-height:20px; }
    .login { margin-top:14px; padding:18px; }
    @media (max-width:780px) { .top{align-items:flex-start; flex-direction:column;} .grid{grid-template-columns:1fr;} .span2{grid-column:auto;} th:nth-child(4),td:nth-child(4){display:none;} }
  </style>
</head>
<body>
  <header>
    <div class="top">
      <div>
        <h1>VPS Traffic Monitoring</h1>
      </div>
      <div class="actions">
        <div class="status"><span class="dot"></span><span id="updated">连接中</span></div>
        <button id="loginToggle" class="secondary">登录</button>
        <button id="logout" class="secondary hidden">退出</button>
      </div>
    </div>
  </header>

  <main>
    <section id="loginPanel" class="panel login hidden">
      <h2>管理员登录</h2>
      <div class="toolbar">
        <label>用户名<input id="username" autocomplete="username" placeholder="admin"></label>
        <label>密码<input id="password" type="password" autocomplete="current-password"></label>
        <button id="login">登录</button>
      </div>
      <div class="message" id="loginMessage"></div>
    </section>

    <section class="grid">
      <div class="panel metric">
        <div class="label">本周期总流量</div>
        <div class="value" id="total">--</div>
        <div class="hint" id="cycle">登录后查看重置周期</div>
      </div>
      <div class="panel metric">
        <div class="label">上行流量</div>
        <div class="value" id="tx">--</div>
        <div class="hint" id="txRate">--</div>
      </div>
      <div class="panel metric">
        <div class="label">下行流量</div>
        <div class="value" id="rx">--</div>
        <div class="hint" id="rxRate">--</div>
      </div>
    </section>

    <section class="grid">
      <div class="panel section span2 admin-panel locked" id="interfacesPanel">
        <h2>网卡明细</h2>
        <table>
          <thead><tr><th>网卡</th><th>下行</th><th>上行</th><th>总计</th><th>当前速度</th></tr></thead>
          <tbody id="interfaces"></tbody>
        </table>
      </div>

      <div class="panel section admin-panel locked" id="settingsPanel">
        <h2>设置</h2>
        <div class="toolbar">
          <label>每月重置日<input id="resetDay" type="number" min="1" max="31"></label>
          <label>统计网卡<input id="iface" placeholder="auto 或 eth0,ens3"></label>
          <button id="save">保存</button>
          <button id="reset" class="secondary">手动重置</button>
        </div>
        <div class="message" id="message"></div>
      </div>
    </section>

    <section class="panel section admin-panel locked" id="manualPanel">
      <h2>手动录入已用流量</h2>
      <div class="toolbar">
        <label>已用下行<input id="manualRx" type="number" min="0" step="0.01" placeholder="0"></label>
        <label>已用上行<input id="manualTx" type="number" min="0" step="0.01" placeholder="0"></label>
        <label>单位<select id="manualUnit"><option>GB</option><option>MB</option><option>TB</option></select></label>
        <button id="manualAdd">添加到本周期</button>
      </div>
      <div class="hint">适合已经使用了半个月后才安装监控的情况。这里录入的是额外已用量，系统会继续叠加后续真实采样。</div>
      <div class="message" id="manualMessage"></div>
    </section>

    <section class="panel section admin-panel locked" id="historyPanel">
      <h2>历史周期</h2>
      <table>
        <thead><tr><th>开始</th><th>结束</th><th>下行</th><th>上行</th><th>总计</th></tr></thead>
        <tbody id="history"></tbody>
      </table>
    </section>
  </main>

  <script>
    const $ = id => document.getElementById(id);
    const fmt = bytes => {
      const units = ['B','KB','MB','GB','TB','PB'];
      let v = Number(bytes || 0), i = 0;
      while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
      return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(2)} ${units[i]}`;
    };
    const rate = bps => `${fmt(bps)}/s`;
    const post = (url, body) => fetch(url, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body || {})
    }).then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || '请求失败');
      return data;
    });
    function setAdmin(admin) {
      document.querySelectorAll('.admin-panel').forEach(el => el.classList.toggle('locked', !admin));
      $('loginToggle').classList.toggle('hidden', admin);
      $('logout').classList.toggle('hidden', !admin);
      if (admin) $('loginPanel').classList.add('hidden');
    }
    async function load() {
      try {
        const data = await fetch('/api/status', {cache:'no-store'}).then(r => r.json());
        setAdmin(Boolean(data.admin));
        $('total').textContent = fmt(data.cycle.total_bytes);
        $('tx').textContent = fmt(data.cycle.tx_bytes);
        $('rx').textContent = fmt(data.cycle.rx_bytes);
        $('txRate').textContent = `当前上行 ${rate(data.rate.tx_bps)}`;
        $('rxRate').textContent = `当前下行 ${rate(data.rate.rx_bps)}`;
        $('cycle').textContent = data.admin ? `${data.cycle.start_local} 至 ${data.cycle.next_reset_local}` : '登录后查看重置周期';
        $('updated').textContent = `已更新 ${new Date(data.now * 1000).toLocaleTimeString()}`;
        if (data.admin) {
          $('interfaces').innerHTML = data.interfaces.map(row => `
            <tr><td>${row.name === 'manual' ? '手动录入' : row.name}</td><td>${fmt(row.rx_bytes)}</td><td>${fmt(row.tx_bytes)}</td><td>${fmt(row.rx_bytes + row.tx_bytes)}</td><td>${rate(row.rx_bps + row.tx_bps)}</td></tr>
          `).join('') || '<tr><td colspan="5">暂无网卡数据</td></tr>';
          $('resetDay').value = data.config.reset_day;
          $('iface').value = data.config.interfaces.length ? data.config.interfaces.join(',') : 'auto';
          $('history').innerHTML = data.history.map(row => `
            <tr><td>${row.start_local}</td><td>${row.end_local}</td><td>${fmt(row.rx_bytes)}</td><td>${fmt(row.tx_bytes)}</td><td>${fmt(row.total_bytes)}</td></tr>
          `).join('') || '<tr><td colspan="5">暂无历史周期</td></tr>';
        }
      } catch (err) {
        $('updated').textContent = '连接失败';
        $('message').textContent = err.message;
      }
    }
    $('loginToggle').onclick = () => $('loginPanel').classList.toggle('hidden');
    $('login').onclick = async () => {
      try {
        await post('/api/login', {username: $('username').value, password: $('password').value});
        $('loginMessage').textContent = '';
        $('password').value = '';
        load();
      } catch (err) { $('loginMessage').textContent = err.message; }
    };
    $('logout').onclick = async () => { await post('/api/logout', {}); load(); };
    $('save').onclick = async () => {
      const interfaces = $('iface').value.trim();
      try {
        await post('/api/config', {
          reset_day: Number($('resetDay').value),
          interfaces: interfaces.toLowerCase() === 'auto' ? [] : interfaces.split(',').map(x => x.trim()).filter(Boolean)
        });
        $('message').textContent = '已保存';
        load();
      } catch (err) { $('message').textContent = err.message; }
    };
    $('reset').onclick = async () => {
      if (!confirm('确定要从现在开始重新计算本周期流量吗？')) return;
      try { await post('/api/reset', {}); $('message').textContent = '已重置'; load(); }
      catch (err) { $('message').textContent = err.message; }
    };
    $('manualAdd').onclick = async () => {
      try {
        await post('/api/manual-usage', {rx: $('manualRx').value, tx: $('manualTx').value, unit: $('manualUnit').value});
        $('manualMessage').textContent = '已添加到本周期';
        $('manualRx').value = '';
        $('manualTx').value = '';
        load();
      } catch (err) { $('manualMessage').textContent = err.message; }
    };
    load();
    setInterval(load, 5000);
  </script>
</body>
</html>
"""


def utc_now() -> int:
    return int(time.time())


def hash_password(password: str, salt=None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def make_session(config: dict, username: str) -> str:
    ts = str(utc_now())
    payload = f"{username}:{ts}"
    sig = hmac.new(config["secret_key"].encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session(config: dict, token: str) -> bool:
    try:
        username, ts_raw, sig = token.split(":", 2)
        ts = int(ts_raw)
    except (ValueError, AttributeError):
        return False
    if username != config.get("admin_user"):
        return False
    if utc_now() - ts > 86400:
        return False
    payload = f"{username}:{ts_raw}"
    expected = hmac.new(config["secret_key"].encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def iso_local(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def month_boundary(ts: int, reset_day: int) -> int:
    dt = datetime.fromtimestamp(ts)
    day = min(reset_day, calendar.monthrange(dt.year, dt.month)[1])
    boundary = dt.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if dt < boundary:
      year, month = (dt.year - 1, 12) if dt.month == 1 else (dt.year, dt.month - 1)
      day = min(reset_day, calendar.monthrange(year, month)[1])
      boundary = boundary.replace(year=year, month=month, day=day)
    return int(boundary.timestamp())


def next_month_boundary(ts: int, reset_day: int) -> int:
    dt = datetime.fromtimestamp(ts)
    year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    day = min(reset_day, calendar.monthrange(year, month)[1])
    boundary = dt.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
    return int(boundary.timestamp())


def load_config(path: str) -> dict:
    defaults = {
        "host": "0.0.0.0",
        "port": DEFAULT_PORT,
        "reset_day": 1,
        "interfaces": [],
        "exclude_interfaces": ["lo", "docker*", "br-*", "veth*", "virbr*", "zt*", "tailscale*", "wg*"],
        "sample_interval": DEFAULT_INTERVAL,
        "database": DEFAULT_DB,
        "admin_user": "admin",
        "admin_password_hash": "",
        "secret_key": "",
    }
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        defaults.update(data)
    defaults["reset_day"] = max(1, min(31, int(defaults.get("reset_day", 1))))
    defaults["port"] = int(defaults.get("port", DEFAULT_PORT))
    defaults["sample_interval"] = max(2, int(defaults.get("sample_interval", DEFAULT_INTERVAL)))
    if not defaults.get("secret_key"):
        defaults["secret_key"] = secrets.token_hex(32)
    return defaults


def save_config(path: str, config: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def glob_match(name: str, pattern: str) -> bool:
    escaped = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(escaped, name) is not None


def read_net_dev(config: dict) -> dict:
    wanted = set(config.get("interfaces") or [])
    excludes = config.get("exclude_interfaces") or []
    result = {}
    with open("/proc/net/dev", "r", encoding="utf-8") as fh:
        for line in fh.readlines()[2:]:
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            name = name.strip()
            fields = raw.split()
            if len(fields) < 16:
                continue
            if wanted:
                if name not in wanted:
                    continue
            elif any(glob_match(name, pat) for pat in excludes):
                continue
            result[name] = {"rx": int(fields[0]), "tx": int(fields[8])}
    return result


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self.lock, self.db:
            self.db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER,
                reset_day INTEGER NOT NULL,
                manual INTEGER NOT NULL DEFAULT 0,
                UNIQUE(start_ts)
              );
              CREATE TABLE IF NOT EXISTS interface_totals (
                cycle_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                rx_bytes INTEGER NOT NULL DEFAULT 0,
                tx_bytes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(cycle_id, name)
              );
              CREATE TABLE IF NOT EXISTS last_counters (
                name TEXT PRIMARY KEY,
                rx_counter INTEGER NOT NULL,
                tx_counter INTEGER NOT NULL,
                seen_ts INTEGER NOT NULL
              );
            """)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def active_cycle(self, reset_day: int, now: int) -> sqlite3.Row:
        start = month_boundary(now, reset_day)
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM cycles WHERE end_ts IS NULL ORDER BY start_ts DESC LIMIT 1").fetchone()
            if row and row["start_ts"] >= start:
                return row
            if row:
                self.db.execute("UPDATE cycles SET end_ts=? WHERE id=?", (start, row["id"]))
            existing = self.db.execute("SELECT * FROM cycles WHERE start_ts=?", (start,)).fetchone()
            if existing:
                self.db.execute("UPDATE cycles SET end_ts=NULL, reset_day=? WHERE id=?", (reset_day, existing["id"]))
                return self.db.execute("SELECT * FROM cycles WHERE id=?", (existing["id"],)).fetchone()
            cur = self.db.execute("INSERT INTO cycles(start_ts, reset_day) VALUES(?, ?)", (start, reset_day))
            return self.db.execute("SELECT * FROM cycles WHERE id=?", (cur.lastrowid,)).fetchone()

    def manual_reset(self, reset_day: int) -> None:
        now = utc_now()
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM cycles WHERE end_ts IS NULL ORDER BY start_ts DESC LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE cycles SET end_ts=? WHERE id=?", (now, row["id"]))
            self.db.execute("INSERT OR IGNORE INTO cycles(start_ts, reset_day, manual) VALUES(?, ?, 1)", (now, reset_day))
            self.db.execute("DELETE FROM last_counters")

    def ingest(self, counters: dict, reset_day: int, now: int) -> None:
        with self.lock, self.db:
            cycle = self.active_cycle(reset_day, now)
            cycle_id = cycle["id"]
            names = set(counters)
            for name, values in counters.items():
                last = self.db.execute("SELECT * FROM last_counters WHERE name=?", (name,)).fetchone()
                rx_delta = tx_delta = 0
                if last:
                    rx_delta = values["rx"] - last["rx_counter"]
                    tx_delta = values["tx"] - last["tx_counter"]
                    if rx_delta < 0 or tx_delta < 0:
                        rx_delta = tx_delta = 0
                    if rx_delta > MAX_REASONABLE_DELTA or tx_delta > MAX_REASONABLE_DELTA:
                        rx_delta = tx_delta = 0
                self.db.execute("""
                  INSERT INTO interface_totals(cycle_id, name, rx_bytes, tx_bytes)
                  VALUES(?, ?, ?, ?)
                  ON CONFLICT(cycle_id, name) DO UPDATE SET
                    rx_bytes = rx_bytes + excluded.rx_bytes,
                    tx_bytes = tx_bytes + excluded.tx_bytes
                """, (cycle_id, name, rx_delta, tx_delta))
                self.db.execute("""
                  INSERT INTO last_counters(name, rx_counter, tx_counter, seen_ts)
                  VALUES(?, ?, ?, ?)
                  ON CONFLICT(name) DO UPDATE SET
                    rx_counter=excluded.rx_counter,
                    tx_counter=excluded.tx_counter,
                    seen_ts=excluded.seen_ts
                """, (name, values["rx"], values["tx"], now))
            if names:
                placeholders = ",".join("?" for _ in names)
                self.db.execute(f"DELETE FROM last_counters WHERE name NOT IN ({placeholders})", tuple(names))

    def add_manual_usage(self, reset_day: int, rx_bytes: int, tx_bytes: int) -> dict:
        if rx_bytes < 0 or tx_bytes < 0:
            raise ValueError("manual usage must not be negative")
        with self.lock, self.db:
            now = utc_now()
            cycle = self.active_cycle(reset_day, now)
            cycle_id = cycle["id"]
            self.db.execute("""
              INSERT INTO interface_totals(cycle_id, name, rx_bytes, tx_bytes)
              VALUES(?, ?, ?, ?)
              ON CONFLICT(cycle_id, name) DO UPDATE SET
                rx_bytes = rx_bytes + excluded.rx_bytes,
                tx_bytes = tx_bytes + excluded.tx_bytes
            """, (cycle_id, "manual", rx_bytes, tx_bytes))
            return {"ok": True, "rx_bytes": rx_bytes, "tx_bytes": tx_bytes}

    def snapshot(self, reset_day: int, rates: dict) -> dict:
        now = utc_now()
        with self.lock:
            cycle = self.active_cycle(reset_day, now)
            rows = self.db.execute("""
              SELECT name, rx_bytes, tx_bytes
              FROM interface_totals
              WHERE cycle_id=?
              ORDER BY name
            """, (cycle["id"],)).fetchall()
            interfaces = []
            for row in rows:
                item_rates = rates.get(row["name"], {"rx_bps": 0, "tx_bps": 0})
                interfaces.append({
                    "name": row["name"],
                    "rx_bytes": row["rx_bytes"],
                    "tx_bytes": row["tx_bytes"],
                    "rx_bps": item_rates["rx_bps"],
                    "tx_bps": item_rates["tx_bps"],
                })
            rx = sum(x["rx_bytes"] for x in interfaces)
            tx = sum(x["tx_bytes"] for x in interfaces)
            history_rows = self.db.execute("""
              SELECT c.start_ts, c.end_ts, COALESCE(SUM(t.rx_bytes), 0) AS rx_bytes,
                     COALESCE(SUM(t.tx_bytes), 0) AS tx_bytes
              FROM cycles c
              LEFT JOIN interface_totals t ON t.cycle_id = c.id
              WHERE c.end_ts IS NOT NULL
              GROUP BY c.id
              ORDER BY c.start_ts DESC
              LIMIT 12
            """).fetchall()
        return {
            "now": now,
            "cycle": {
                "start_ts": cycle["start_ts"],
                "start_local": iso_local(cycle["start_ts"]),
                "next_reset_ts": next_month_boundary(cycle["start_ts"], reset_day),
                "next_reset_local": iso_local(next_month_boundary(cycle["start_ts"], reset_day)),
                "rx_bytes": rx,
                "tx_bytes": tx,
                "total_bytes": rx + tx,
            },
            "rate": {
                "rx_bps": sum(v["rx_bps"] for v in rates.values()),
                "tx_bps": sum(v["tx_bps"] for v in rates.values()),
            },
            "interfaces": interfaces,
            "history": [{
                "start_local": iso_local(row["start_ts"]),
                "end_local": iso_local(row["end_ts"]),
                "rx_bytes": row["rx_bytes"],
                "tx_bytes": row["tx_bytes"],
                "total_bytes": row["rx_bytes"] + row["tx_bytes"],
            } for row in history_rows],
        }


class Collector(threading.Thread):
    def __init__(self, config_path: str, store: Store):
        super().__init__(daemon=True)
        self.config_path = config_path
        self.store = store
        self.stop_event = threading.Event()
        self.rates = {}
        self.previous = None
        self.previous_ts = None
        self.lock = threading.RLock()

    def get_rates(self) -> dict:
        with self.lock:
            return dict(self.rates)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                config = load_config(self.config_path)
                now = utc_now()
                counters = read_net_dev(config)
                next_rates = {}
                if self.previous and self.previous_ts:
                    elapsed = max(1, now - self.previous_ts)
                    for name, values in counters.items():
                        old = self.previous.get(name)
                        if not old:
                            next_rates[name] = {"rx_bps": 0, "tx_bps": 0}
                            continue
                        rx_delta = values["rx"] - old["rx"]
                        tx_delta = values["tx"] - old["tx"]
                        if rx_delta < 0 or tx_delta < 0:
                            rx_delta = tx_delta = 0
                        next_rates[name] = {"rx_bps": int(rx_delta / elapsed), "tx_bps": int(tx_delta / elapsed)}
                with self.lock:
                    self.rates = next_rates
                self.store.ingest(counters, config["reset_day"], now)
                self.previous = counters
                self.previous_ts = now
                self.stop_event.wait(config["sample_interval"])
            except Exception as exc:
                print(f"{APP_NAME}: collector error: {exc}", flush=True)
                self.stop_event.wait(DEFAULT_INTERVAL)


def make_handler(config_path: str, store: Store, collector: Collector):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP_NAME

        def send_json(self, status: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def cookie_value(self, name: str) -> str:
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                if "=" not in part:
                    continue
                key, value = part.strip().split("=", 1)
                if key == name:
                    return value
            return ""

        def is_admin(self, config: dict) -> bool:
            return verify_session(config, self.cookie_value("vps_tm_session"))

        def require_admin(self, config: dict) -> None:
            if not self.is_admin(config):
                raise PermissionError("login required")

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/status":
                config = load_config(config_path)
                admin = self.is_admin(config)
                data = store.snapshot(config["reset_day"], collector.get_rates())
                data["admin"] = admin
                if admin:
                    data["config"] = {
                        "reset_day": config["reset_day"],
                        "interfaces": config.get("interfaces") or [],
                        "port": config["port"],
                        "admin_user": config.get("admin_user", "admin"),
                    }
                else:
                    data.pop("history", None)
                    data["interfaces"] = []
                    data["cycle"].pop("start_local", None)
                    data["cycle"].pop("next_reset_local", None)
                self.send_json(200, data)
            elif parsed.path == "/health":
                self.send_json(200, {"ok": True})
            else:
                self.send_error(404)

        def do_POST(self):
            try:
                parsed = urlparse(self.path)
                data = self.read_json()
                config = load_config(config_path)
                if parsed.path == "/api/login":
                    username = str(data.get("username", ""))
                    password = str(data.get("password", ""))
                    if username != config.get("admin_user") or not verify_password(password, config.get("admin_password_hash", "")):
                        raise PermissionError("invalid username or password")
                    token = make_session(config, username)
                    body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", f"vps_tm_session={token}; Path=/; HttpOnly; SameSite=Lax")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/logout":
                    body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", "vps_tm_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/config":
                    self.require_admin(config)
                    reset_day = int(data.get("reset_day", config["reset_day"]))
                    if reset_day < 1 or reset_day > 31:
                        raise ValueError("重置日必须在 1 到 31 之间")
                    interfaces = data.get("interfaces", config.get("interfaces") or [])
                    if interfaces is None:
                        interfaces = []
                    if not isinstance(interfaces, list) or not all(isinstance(x, str) for x in interfaces):
                        raise ValueError("网卡列表格式错误")
                    config["reset_day"] = reset_day
                    config["interfaces"] = [x.strip() for x in interfaces if x.strip()]
                    save_config(config_path, config)
                    self.send_json(200, {"ok": True})
                elif parsed.path == "/api/reset":
                    self.require_admin(config)
                    store.manual_reset(config["reset_day"])
                    self.send_json(200, {"ok": True})
                elif parsed.path == "/api/manual-usage":
                    self.require_admin(config)
                    unit = str(data.get("unit", "GB")).upper()
                    multiplier = {"MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}.get(unit)
                    if not multiplier:
                        raise ValueError("unit must be MB, GB, or TB")
                    rx_bytes = int(float(data.get("rx", 0) or 0) * multiplier)
                    tx_bytes = int(float(data.get("tx", 0) or 0) * multiplier)
                    self.send_json(200, store.add_manual_usage(config["reset_day"], rx_bytes, tx_bytes))
                else:
                    self.send_error(404)
            except Exception as exc:
                status = 403 if isinstance(exc, PermissionError) else 400
                self.send_json(status, {"error": str(exc)})

        def log_message(self, fmt, *args):
            print(f"{self.address_string()} - {fmt % args}", flush=True)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="VPS traffic monitoring web service")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if not os.path.exists("/proc/net/dev"):
        raise SystemExit("This service must run on Linux with /proc/net/dev")
    config = load_config(args.config)
    save_config(args.config, config)
    store = Store(config["database"])
    collector = Collector(args.config, store)
    collector.start()
    handler = make_handler(args.config, store, collector)
    httpd = ThreadingHTTPServer((config["host"], config["port"]), handler)

    def shutdown(signum, frame):
        collector.stop_event.set()
        httpd.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"{APP_NAME}: listening on {config['host']}:{config['port']}", flush=True)
    httpd.serve_forever()
    collector.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
