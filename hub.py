#!/usr/bin/env python3
"""VPS Traffic Hub（主控端）。

当前实现：
- 阶段 1：HubStore（meta/nodes/samples）、token 鉴权、POST /api/report、增量落库、CLI 建节点。
- 阶段 3：daily_usage 每日聚合（按节点本地时区，默认北京时间 UTC+8）、近 7 日统计、公开 Dashboard。
- 阶段 4：按节点阈值预测（本周期已用 + 近 7 日日均 × 剩余天数），预计超限写 alerts 表并展示。
后续阶段：管理员登录与节点管理 UI、SMTP 邮件告警、安装脚本。
"""
import argparse
import base64
import calendar
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import signal
import smtplib
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from email.message import EmailMessage
from urllib.parse import urlparse

APP_NAME = "vps-traffic-hub"
DEFAULT_CONFIG = "/etc/vps-traffic-hub/config.json"
DEFAULT_PORT = 8898
REPO_RAW = "https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main"
ONLINE_THRESHOLD_SECONDS = 180

DEFAULTS = {
    "host": "0.0.0.0",
    "port": DEFAULT_PORT,
    "database": "/var/lib/vps-traffic-hub/hub.db",
    "admin_user": "admin",
    "admin_password_hash": "",
    "secret_key": "",
    "default_tz_offset_minutes": 480,  # 统一默认北京时间 UTC+8
    "smtp": {
        "enabled": False,
        "host": "smtp.gmail.com",
        "port": 587,
        "username": "",
        "password": "",
        "from_addr": "",
        "to_addrs": [],
        "use_ssl": False,
        "use_starttls": True,
    },
    "alert_cooldown_hours": 24,
    "recovery_email_enabled": True,
    "dashboard_url": "",
    "hub_public_url": "",
    "check_interval": 300,
}

DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VPS Traffic Hub</title>
  <style>
    :root { color-scheme: dark light; --bg:#10141d; --panel:#171d29; --text:#edf2f7; --muted:#9fb0ca; --line:#2b3445; --accent:#34b6b8; --good:#55c796; --warn:#e0714a; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    header { padding:20px clamp(16px,4vw,42px) 14px; border-bottom:1px solid var(--line); background:var(--panel); }
    .top { max-width:1180px; margin:0 auto; display:flex; gap:14px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
    h1 { margin:0; font-size:24px; }
    h2 { margin:24px 0 10px; font-size:18px; }
    .sub { color:var(--muted); font-size:12px; }
    .actions { display:flex; gap:8px; align-items:center; }
    main { width:min(1180px, calc(100% - 32px)); margin:18px auto 40px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:14px; }
    table { width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    th, td { padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }
    th { color:var(--muted); font-weight:650; }
    tr:last-child td { border-bottom:0; }
    .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
    .dot.on { background:var(--good); box-shadow:0 0 0 4px rgba(85,199,150,.18); }
    .dot.off { background:var(--warn); }
    .badge { display:inline-block; padding:2px 8px; border-radius:6px; font-size:12px; }
    .badge.warn { background:rgba(224,113,74,.16); color:var(--warn); }
    .badge.ok { background:rgba(85,199,150,.16); color:var(--good); }
    .bars { display:flex; gap:4px; align-items:flex-end; height:54px; min-width:220px; }
    .bar-col { display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; gap:3px; }
    .bar { width:14px; min-height:2px; background:var(--accent); border-radius:2px; }
    .bar-col span { font-size:10px; color:var(--muted); }
    .iface { color:var(--muted); font-size:12px; margin-top:2px; }
    .form { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; align-items:end; }
    label { display:grid; gap:5px; color:var(--muted); }
    input, select, button { height:36px; border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--text); padding:0 10px; font:inherit; }
    button { cursor:pointer; background:var(--accent); color:#fff; border-color:transparent; font-weight:700; }
    button.secondary { background:transparent; color:var(--text); border-color:var(--line); }
    button.danger { background:transparent; color:var(--warn); border-color:var(--line); }
    .message { color:var(--warn); min-height:20px; margin-top:8px; }
    .cmd { width:100%; min-height:64px; background:#0b0e15; color:var(--good); border:1px solid var(--line); border-radius:8px; padding:10px; font:12px/1.6 ui-monospace,Consolas,monospace; white-space:pre-wrap; word-break:break-all; }
    .hidden { display:none !important; }
    @media (max-width:760px) { .top { flex-direction:column; align-items:flex-start; } th:nth-child(4), td:nth-child(4) { display:none; } .bars { min-width:160px; } }
  </style>
</head>
<body>
  <header>
    <div class="top">
      <div>
        <h1>VPS Traffic Hub</h1>
        <div class="sub">多节点流量监控 · 近 7 日统计 · 阈值告警</div>
      </div>
      <div class="actions">
        <span class="sub" id="updated">连接中</span>
        <button id="loginToggle" class="secondary">登录</button>
        <button id="logout" class="secondary hidden">退出</button>
      </div>
    </div>
  </header>

  <main>
    <section id="loginPanel" class="panel hidden">
      <h2>管理员登录</h2>
      <div class="form" style="grid-template-columns:180px 180px auto;">
        <label>用户名<input id="username" autocomplete="username" placeholder="admin"></label>
        <label>密码<input id="password" type="password" autocomplete="current-password"></label>
        <button id="loginBtn">登录</button>
      </div>
      <div class="message" id="loginMessage"></div>
    </section>

    <section id="adminSection" class="hidden">
      <div class="panel">
        <h2 id="nodeFormTitle">添加 VPS</h2>
        <div class="form">
          <label>备注名称 *<input id="nName" placeholder="例如：香港VPS"></label>
          <label>主机 / IP<input id="nHost" placeholder="可选"></label>
          <label>每月重置日<input id="nResetDay" type="number" min="1" max="31" value="1"></label>
          <label>流量阈值 GB（0=未启用）<input id="nThreshold" type="number" min="0" step="0.01" value="0"></label>
          <label>收件邮箱（可选）<input id="nEmail" placeholder="留空使用全局"></label>
          <button id="nodeSave">添加</button>
          <button id="nodeCancel" class="secondary hidden">取消编辑</button>
        </div>
        <div class="message" id="nodeMessage"></div>
      </div>

      <div class="panel">
        <h2>安装命令</h2>
        <div class="sub">添加 VPS 后，把下面命令复制到被控 VPS 上以 root 执行即可。</div>
        <div class="cmd" id="installCmd">添加 VPS 后生成安装命令</div>
      </div>

      <div class="panel">
        <h2>节点管理</h2>
        <table>
          <thead><tr><th>ID</th><th>备注名称</th><th>主机/IP</th><th>重置日</th><th>阈值(GB)</th><th>收件邮箱</th><th>操作</th></tr></thead>
          <tbody id="nodeAdmin"></tbody>
        </table>
      </div>

      <div class="panel">
        <h2>SMTP 邮件设置</h2>
        <div class="form">
          <label>启用<input id="sEnabled" type="checkbox" style="height:20px;"></label>
          <label>SMTP 主机<input id="sHost" placeholder="smtp.gmail.com"></label>
          <label>端口<input id="sPort" type="number" value="587"></label>
          <label>用户名<input id="sUsername"></label>
          <label>密码 / 授权码<input id="sPassword" type="password" placeholder="留空不修改"></label>
          <label>发件邮箱<input id="sFrom"></label>
          <label>收件邮箱（逗号分隔）<input id="sTo"></label>
          <label>STARTTLS<input id="sStarttls" type="checkbox" checked style="height:20px;"></label>
          <label>SSL<input id="sSsl" type="checkbox" style="height:20px;"></label>
          <button id="smtpSave">保存</button>
          <button id="smtpTest" class="secondary">发送测试邮件</button>
        </div>
        <div class="message" id="smtpMessage"></div>
      </div>
    </section>

    <table>
      <thead><tr><th>节点</th><th>状态</th><th>本周期已用</th><th>近 7 日合计</th><th>阈值 / 预测</th><th>近 7 日趋势</th></tr></thead>
      <tbody id="tbody"></tbody>
    </table>

    <h2>告警记录</h2>
    <table>
      <thead><tr><th>时间</th><th>节点</th><th>状态</th><th>预计总量</th><th>阈值</th><th>剩余天数</th><th>预计超限日</th></tr></thead>
      <tbody id="alerts"></tbody>
    </table>
  </main>

  <script>
    const $ = id => document.getElementById(id);
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const fmt = bytes => { const units=['B','KB','MB','GB','TB','PB']; let v=Number(bytes||0), i=0; while(v>=1024 && i<units.length-1){v/=1024;i++;} return `${v>=100||i===0?v.toFixed(0):v.toFixed(2)} ${units[i]}`; };
    const gb = bytes => bytes ? (bytes/1024/1024/1024).toFixed(3) : '0';
    const timeFmt = ts => new Date(ts*1000).toLocaleString();
    let S = { admin:false, nodes:[], alerts:[], smtp:{}, editingId:null };

    async function api(url, body) {
      const r = await fetch(url, { method: body ? 'POST' : 'GET', headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined });
      const d = await r.json().catch(()=>({}));
      if (!r.ok) throw new Error(d.error || '请求失败');
      return d;
    }
    function setAdmin(admin) {
      S.admin = admin;
      $('adminSection').classList.toggle('hidden', !admin);
      $('loginToggle').classList.toggle('hidden', admin);
      $('logout').classList.toggle('hidden', !admin);
      if (admin) $('loginPanel').classList.add('hidden');
    }
    function predCell(n) {
      const p = n.prediction;
      if (!p) return '<span class="sub">未设置</span>';
      const status = p.status === 'ok' ? '<span class="badge ok">正常</span>' : '<span class="badge warn">预计超限</span>';
      return `${status} ${fmt(p.projected_total_bytes)} / ${fmt(p.threshold_bytes)}`;
    }
    function nodeRows(nodes) {
      return nodes.map(n => {
        const daily = n.daily || [];
        const max = Math.max(1, ...daily.map(d=>d.total_bytes));
        const bars = daily.map(d=>`<div class="bar-col"><div class="bar" style="height:${(d.total_bytes/max*100).toFixed(0)}%" title="${esc(d.day)} ${fmt(d.total_bytes)}"></div><span>${esc(d.day.slice(5))}</span></div>`).join('');
        const ifaces = (n.interfaces||[]).map(i=>`${esc(i.name)}: ${fmt((i.rx_bytes||0)+(i.tx_bytes||0))}`).join(' / ');
        const total7 = daily.reduce((s,d)=>s+d.total_bytes,0);
        return `<tr><td><strong>${esc(n.name)}</strong><div class="sub">${esc(n.host)}</div></td><td><span class="dot ${n.online?'on':'off'}"></span>${n.online?'在线':'离线'}</td><td>${fmt(n.current_total_bytes)}</td><td>${fmt(total7)}</td><td>${predCell(n)}</td><td><div class="bars">${bars}</div><div class="iface">${ifaces||'—'}</div></td></tr>`;
      }).join('');
    }
    function adminRows(nodes) {
      return nodes.map(n => `<tr>
        <td>${n.id}</td><td>${esc(n.name)}</td><td>${esc(n.host)}</td><td>${n.reset_day}</td><td>${gb(n.threshold_bytes)}</td><td>${esc(n.alert_email||'全局')}</td>
        <td>
          <button class="secondary" onclick="editNode(${n.id})">编辑</button>
          <button class="secondary" onclick="showToken(${n.id})">安装命令</button>
          <button class="danger" onclick="delNode(${n.id})">删除</button>
        </td></tr>`).join('');
    }
    function alertRows(alerts) {
      return (alerts||[]).map(a=>`<tr><td>${timeFmt(a.ts)}</td><td>${esc(a.node_name)}</td><td>${a.status==='exceeded'?'已超限':(a.status==='recovered'?'已恢复':'预计超限')}</td><td>${fmt(a.predicted_total_bytes)}</td><td>${fmt(a.threshold_bytes)}</td><td>${a.days_left}</td><td>${esc(a.projected_exceed_day||'—')}</td></tr>`).join('');
    }
    function fillSmtp() {
      $('sEnabled').checked = !!S.smtp.enabled;
      $('sHost').value = S.smtp.host || '';
      $('sPort').value = S.smtp.port || 587;
      $('sUsername').value = S.smtp.username || '';
      $('sFrom').value = S.smtp.from_addr || '';
      $('sTo').value = (S.smtp.to_addrs || []).join(',');
      $('sStarttls').checked = !!S.smtp.use_starttls;
      $('sSsl').checked = !!S.smtp.use_ssl;
    }
    async function load() {
      try {
        const data = await api('/api/status');
        S.nodes = data.nodes || []; S.alerts = data.alerts || []; S.smtp = data.smtp || {};
        setAdmin(!!data.admin);
        $('updated').textContent = `已更新 ${new Date(data.now*1000).toLocaleTimeString()}`;
        $('tbody').innerHTML = nodeRows(S.nodes) || '<tr><td colspan="6">暂无节点</td></tr>';
        $('alerts').innerHTML = alertRows(S.alerts) || '<tr><td colspan="7">暂无告警</td></tr>';
        if (S.admin) { $('nodeAdmin').innerHTML = adminRows(S.nodes) || '<tr><td colspan="7">暂无节点</td></tr>'; fillSmtp(); }
      } catch (e) { $('updated').textContent = '连接失败'; }
    }
    function showCommand(cmd) { $('installCmd').textContent = cmd; }
    function resetNodeForm() {
      S.editingId = null; $('nodeFormTitle').textContent = '添加 VPS'; $('nodeSave').textContent = '添加';
      $('nodeCancel').classList.add('hidden');
      $('nName').value=''; $('nHost').value=''; $('nResetDay').value=1; $('nThreshold').value=0; $('nEmail').value='';
    }
    window.editNode = id => {
      const n = S.nodes.find(x => x.id === id); if (!n) return;
      S.editingId = id; $('nodeFormTitle').textContent = `编辑节点 #${id}`; $('nodeSave').textContent = '保存修改'; $('nodeCancel').classList.remove('hidden');
      $('nName').value=n.name||''; $('nHost').value=n.host||''; $('nResetDay').value=n.reset_day; $('nThreshold').value=gb(n.threshold_bytes); $('nEmail').value=n.alert_email||'';
      $('nodeMessage').textContent='';
    };
    window.showToken = async id => {
      try { const r = await api(`/api/nodes/${id}/token`, {}); showCommand(r.command); } catch(e) { alert(e.message); }
    };
    window.delNode = async id => {
      if (!confirm('确定删除该节点及其历史数据吗？')) return;
      try { await api(`/api/nodes/${id}/delete`, {}); resetNodeForm(); load(); } catch(e) { alert(e.message); }
    };

    $('loginToggle').onclick = () => $('loginPanel').classList.toggle('hidden');
    $('loginBtn').onclick = async () => {
      try { await api('/api/login', {username:$('username').value, password:$('password').value}); $('loginMessage').textContent=''; $('password').value=''; load(); }
      catch(e) { $('loginMessage').textContent = e.message; }
    };
    $('logout').onclick = async () => { await api('/api/logout', {}); load(); };
    $('nodeSave').onclick = async () => {
      const body = { name:$('nName').value.trim(), host:$('nHost').value.trim(), reset_day:Number($('nResetDay').value), threshold_gb:Number($('nThreshold').value||0), email:$('nEmail').value.trim() };
      try {
        if (S.editingId) { await api(`/api/nodes/${S.editingId}/update`, body); $('nodeMessage').textContent='已保存'; }
        else { const r = await api('/api/nodes', body); showCommand(r.command); $('nodeMessage').textContent='已添加，安装命令见下方'; }
        resetNodeForm(); load();
      } catch(e) { $('nodeMessage').textContent = e.message; }
    };
    $('nodeCancel').onclick = () => { resetNodeForm(); $('nodeMessage').textContent=''; };
    $('smtpSave').onclick = async () => {
      try { await api('/api/smtp', { enabled:$('sEnabled').checked, host:$('sHost').value.trim(), port:Number($('sPort').value), username:$('sUsername').value.trim(), password:$('sPassword').value, from_addr:$('sFrom').value.trim(), to_addrs:$('sTo').value, use_ssl:$('sSsl').checked, use_starttls:$('sStarttls').checked }); $('smtpMessage').textContent='已保存'; $('sPassword').value=''; load(); }
      catch(e) { $('smtpMessage').textContent = e.message; }
    };
    $('smtpTest').onclick = async () => {
      try { await api('/api/test-email', {}); $('smtpMessage').textContent='测试邮件已发送'; }
      catch(e) { $('smtpMessage').textContent = e.message; }
    };
    load(); setInterval(load, 5000);
  </script>
</body>
</html>
"""




def utc_now() -> int:
    return int(time.time())


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str, salt=None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$", 2)
    except (ValueError, AttributeError):
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
    expected = hmac.new(config["secret_key"].encode("utf-8"), f"{username}:{ts_raw}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def format_bytes(num: int) -> str:
    """把字节数格式化为易读字符串。"""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num or 0)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0 or value >= 100:
        return f"{value:.0f} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


def send_email(smtp: dict, subject: str, body: str, to_addrs=None) -> None:
    """通过标准库 smtplib 发送邮件。支持 SSL 与 STARTTLS。"""
    host = str(smtp.get("host") or "").strip()
    from_addr = str(smtp.get("from_addr") or "").strip()
    to = list(to_addrs or smtp.get("to_addrs") or [])
    if not host or not from_addr or not to:
        raise ValueError("SMTP 配置不完整（host/from_addr/to_addrs）")
    username = str(smtp.get("username") or from_addr)
    password = str(smtp.get("password") or "")
    port = int(smtp.get("port") or 587)
    use_ssl = bool(smtp.get("use_ssl"))
    use_starttls = bool(smtp.get("use_starttls", not use_ssl))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        if use_starttls:
            server.starttls()
    try:
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    finally:
        server.quit()


def load_config(path: str) -> dict:
    """读取配置并补齐默认值。不落盘（落盘由 save_config 显式完成）。"""
    config = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            config.update(json.load(fh))
    config["port"] = int(config.get("port", DEFAULT_PORT))
    config["host"] = str(config.get("host", "0.0.0.0"))
    if not config.get("secret_key"):
        config["secret_key"] = secrets.token_hex(32)
    return config


def save_config(path: str, config: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def node_local_day(ts: int, tz_offset_minutes: int) -> str:
    """把 UTC 时间戳按节点时区切成 YYYY-MM-DD。"""
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def next_reset_local(ts: int, reset_day: int, tz_offset_minutes: int) -> datetime:
    """节点本地时区下，下一个流量重置日的 00:00。"""
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    dt = datetime.fromtimestamp(ts, tz)
    if dt.day < reset_day:
        year, month = (dt.year - 1, 12) if dt.month == 1 else (dt.year, dt.month - 1)
    else:
        year, month = dt.year, dt.month
    day = min(reset_day, calendar.monthrange(year, month)[1])
    cycle_start = datetime(year, month, day, tzinfo=tz)
    nyear, nmonth = (year + 1, 1) if month == 12 else (year, month + 1)
    nday = min(reset_day, calendar.monthrange(nyear, nmonth)[1])
    return datetime(nyear, nmonth, nday, tzinfo=tz)


class HubStore:
    def __init__(self, db_path: str, smtp_config: dict = None, alert_cooldown_hours: int = 24,
                 recovery_email_enabled: bool = True, dashboard_url: str = ""):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.smtp_config = smtp_config or {}
        self.alert_cooldown_hours = max(0, int(alert_cooldown_hours))
        self.recovery_email_enabled = bool(recovery_email_enabled)
        self.dashboard_url = str(dashboard_url or "")
        self._last_email = {}
        self.init_schema()

    def init_schema(self) -> None:
        with self.lock, self.db:
            self.db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
              );
              INSERT INTO meta(key, value) VALUES('schema_version', '4')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value;
              CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL DEFAULT '',
                token_hash TEXT NOT NULL UNIQUE,
                reset_day INTEGER NOT NULL DEFAULT 1,
                threshold_bytes INTEGER NOT NULL DEFAULT 0,
                alert_email TEXT NOT NULL DEFAULT '',
                tz_offset_minutes INTEGER NOT NULL DEFAULT 480,
                last_seen_ts INTEGER,
                last_cycle_start_ts INTEGER,
                last_rx_bytes INTEGER,
                last_tx_bytes INTEGER,
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                cycle_start_ts INTEGER NOT NULL,
                rx_bytes INTEGER NOT NULL,
                tx_bytes INTEGER NOT NULL,
                delta_rx_bytes INTEGER NOT NULL DEFAULT 0,
                delta_tx_bytes INTEGER NOT NULL DEFAULT 0,
                interfaces_json TEXT,
                raw_json TEXT
              );
              CREATE INDEX IF NOT EXISTS idx_samples_node_ts ON samples(node_id, ts);
              CREATE TABLE IF NOT EXISTS daily_usage (
                node_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                rx_bytes INTEGER NOT NULL DEFAULT 0,
                tx_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(node_id, day)
              );
              CREATE INDEX IF NOT EXISTS idx_daily_usage_node_day ON daily_usage(node_id, day);
              CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                status TEXT NOT NULL,
                predicted_total_bytes INTEGER NOT NULL DEFAULT 0,
                threshold_bytes INTEGER NOT NULL DEFAULT 0,
                avg_daily_bytes INTEGER NOT NULL DEFAULT 0,
                days_left INTEGER NOT NULL DEFAULT 0,
                projected_exceed_day TEXT,
                detail TEXT
              );
              CREATE INDEX IF NOT EXISTS idx_alerts_node_ts ON alerts(node_id, ts);
            """)
            cols = {row[1] for row in self.db.execute("PRAGMA table_info(nodes)")}
            if "token" not in cols:
                self.db.execute("ALTER TABLE nodes ADD COLUMN token TEXT")

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def add_node(self, name: str, host: str = "", reset_day: int = 1,
                 threshold_bytes: int = 0, alert_email: str = "",
                 tz_offset_minutes: int = 480) -> str:
        """新建节点并返回明文 token（只此一次可见）。Hub 只保存 sha256。"""
        if not name or not name.strip():
            raise ValueError("节点名称不能为空")
        reset_day = max(1, min(31, int(reset_day)))
        threshold_bytes = max(0, int(threshold_bytes))
        token = secrets.token_urlsafe(32)
        now = utc_now()
        with self.lock, self.db:
            self.db.execute("""
              INSERT INTO nodes(name, host, token, token_hash, reset_day, threshold_bytes,
                                alert_email, tz_offset_minutes, created_ts, updated_ts)
              VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name.strip(), host.strip(), token, hash_token(token), reset_day,
                  threshold_bytes, alert_email.strip(), tz_offset_minutes, now, now))
            return token

    def update_node(self, node_id: int, reset_day=None, threshold_bytes=None,
                    alert_email=None, name=None, host=None) -> None:
        node = self.get_node(node_id)
        if not node:
            raise ValueError("节点不存在")
        reset_day = node["reset_day"] if reset_day is None else max(1, min(31, int(reset_day)))
        threshold_bytes = node["threshold_bytes"] if threshold_bytes is None else max(0, int(threshold_bytes))
        alert_email = node["alert_email"] if alert_email is None else str(alert_email).strip()
        name = node["name"] if name is None else str(name).strip()
        host = node["host"] if host is None else str(host).strip()
        if not name:
            raise ValueError("节点名称不能为空")
        with self.lock, self.db:
            self.db.execute("""
              UPDATE nodes SET name=?, host=?, reset_day=?, threshold_bytes=?, alert_email=?, updated_ts=?
              WHERE id=?
            """, (name, host, reset_day, threshold_bytes, alert_email, utc_now(), node_id))

    def delete_node(self, node_id: int) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM samples WHERE node_id=?", (node_id,))
            self.db.execute("DELETE FROM daily_usage WHERE node_id=?", (node_id,))
            self.db.execute("DELETE FROM alerts WHERE node_id=?", (node_id,))
            self.db.execute("DELETE FROM nodes WHERE id=?", (node_id,))

    def regenerate_token(self, node_id: int) -> str:
        node = self.get_node(node_id)
        if not node:
            raise ValueError("节点不存在")
        token = secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("UPDATE nodes SET token=?, token_hash=?, updated_ts=? WHERE id=?",
                            (token, hash_token(token), utc_now(), node_id))
        return token

    def get_node_by_token(self, token: str):
        with self.lock:
            return self.db.execute(
                "SELECT * FROM nodes WHERE token_hash=?", (hash_token(token),)
            ).fetchone()

    def get_node(self, node_id: int):
        with self.lock:
            return self.db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()

    def list_nodes(self):
        with self.lock:
            return self.db.execute("SELECT * FROM nodes ORDER BY id").fetchall()

    def last_sample(self, node_id: int):
        with self.lock:
            return self.db.execute(
                "SELECT * FROM samples WHERE node_id=? ORDER BY id DESC LIMIT 1", (node_id,)
            ).fetchone()

    def daily_usage(self, node_id: int, days: int = 7):
        with self.lock:
            return self.db.execute(
                "SELECT day, rx_bytes, tx_bytes, total_bytes FROM daily_usage "
                "WHERE node_id=? ORDER BY day DESC LIMIT ?", (node_id, days)
            ).fetchall()

    def latest_alert(self, node_id: int):
        with self.lock:
            return self.db.execute(
                "SELECT * FROM alerts WHERE node_id=? ORDER BY id DESC LIMIT 1", (node_id,)
            ).fetchone()

    def recent_alerts(self, limit: int = 20):
        with self.lock:
            return self.db.execute("""
              SELECT a.*, n.name AS node_name
              FROM alerts a JOIN nodes n ON n.id = a.node_id
              ORDER BY a.ts DESC, a.id DESC LIMIT ?
            """, (limit,)).fetchall()

    def ingest_report(self, payload: dict) -> dict:
        """校验并保存一条 Agent 上报，累加每日用量，返回本次增量。"""
        token = str(payload.get("token", ""))
        node = self.get_node_by_token(token)
        if not node:
            raise PermissionError("invalid agent token")

        cycle = payload.get("cycle") or {}
        cycle_start_ts = int(cycle.get("start_ts") or 0)
        rx_bytes = int(cycle.get("rx_bytes") or 0)
        tx_bytes = int(cycle.get("tx_bytes") or 0)
        if cycle_start_ts <= 0 or rx_bytes < 0 or tx_bytes < 0:
            raise ValueError("cycle 字段格式错误")

        reset_day = max(1, min(31, int(payload.get("reset_day") or node["reset_day"])))
        tz_offset = int(payload.get("tz_offset_minutes", node["tz_offset_minutes"]))
        hostname = str(payload.get("hostname") or "").strip()
        interfaces = payload.get("interfaces") or []
        report_ts = int(payload.get("ts") or utc_now())

        last_cycle = node["last_cycle_start_ts"]
        last_rx = node["last_rx_bytes"]
        last_tx = node["last_tx_bytes"]
        if last_cycle is not None and last_cycle == cycle_start_ts and last_rx is not None:
            delta_rx = max(0, rx_bytes - last_rx)
            delta_tx = max(0, tx_bytes - last_tx)
        else:
            delta_rx = 0
            delta_tx = 0

        day = node_local_day(report_ts, tz_offset)

        with self.lock, self.db:
            self.db.execute("""
              UPDATE nodes SET
                host=CASE WHEN ? != '' THEN ? ELSE host END,
                reset_day=?, tz_offset_minutes=?, last_seen_ts=?,
                last_cycle_start_ts=?, last_rx_bytes=?, last_tx_bytes=?,
                updated_ts=?
              WHERE id=?
            """, (hostname, hostname, reset_day, tz_offset, utc_now(),
                  cycle_start_ts, rx_bytes, tx_bytes, utc_now(), node["id"]))
            self.db.execute("""
              INSERT INTO samples(node_id, ts, cycle_start_ts, rx_bytes, tx_bytes,
                                  delta_rx_bytes, delta_tx_bytes, interfaces_json, raw_json)
              VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (node["id"], report_ts, cycle_start_ts, rx_bytes, tx_bytes,
                  delta_rx, delta_tx,
                  json.dumps(interfaces, ensure_ascii=False),
                  json.dumps(payload, ensure_ascii=False)))
            if delta_rx or delta_tx:
                self.db.execute("""
                  INSERT INTO daily_usage(node_id, day, rx_bytes, tx_bytes, total_bytes)
                  VALUES(?, ?, ?, ?, ?)
                  ON CONFLICT(node_id, day) DO UPDATE SET
                    rx_bytes = rx_bytes + excluded.rx_bytes,
                    tx_bytes = tx_bytes + excluded.tx_bytes,
                    total_bytes = total_bytes + excluded.total_bytes
                """, (node["id"], day, delta_rx, delta_tx, delta_rx + delta_tx))

        self.evaluate_node(node["id"])
        return {
            "node_id": node["id"],
            "name": node["name"],
            "delta_rx_bytes": delta_rx,
            "delta_tx_bytes": delta_tx,
        }

    def compute_prediction(self, node) -> dict:
        threshold = node["threshold_bytes"] or 0
        if threshold <= 0:
            return None
        daily = [dict(row) for row in self.daily_usage(node["id"], 7)]
        if not daily:
            return None
        avg_daily = int(sum(row["total_bytes"] for row in daily) / len(daily))
        current_total = (node["last_rx_bytes"] or 0) + (node["last_tx_bytes"] or 0)
        now = utc_now()
        next_reset = next_reset_local(now, node["reset_day"], node["tz_offset_minutes"])
        today = datetime.fromtimestamp(now, timezone(timedelta(minutes=node["tz_offset_minutes"]))).date()
        days_left = max(0, (next_reset.date() - today).days)
        projected = current_total + avg_daily * days_left

        if current_total >= threshold:
            status = "exceeded"
            exceed_date = today
        elif projected > threshold:
            status = "warning"
            exceed_in = math.ceil((threshold - current_total) / avg_daily) if avg_daily > 0 else 0
            exceed_date = today + timedelta(days=exceed_in)
        else:
            status = "ok"
            exceed_date = None

        return {
            "threshold_bytes": threshold,
            "current_total_bytes": current_total,
            "avg_daily_bytes": avg_daily,
            "days_left": days_left,
            "projected_total_bytes": projected,
            "projected_exceed_day": exceed_date.isoformat() if exceed_date else None,
            "status": status,
            "triggered": status in ("warning", "exceeded"),
        }

    def evaluate_node(self, node_id: int):
        node = self.get_node(node_id)
        if not node:
            return None
        pred = self.compute_prediction(node)
        latest = self.latest_alert(node_id)

        if pred and pred["triggered"]:
            if latest and latest["status"] == pred["status"] and latest["projected_exceed_day"] == pred["projected_exceed_day"]:
                return None
            alert = self._insert_alert(node_id, pred, pred["status"])
            if alert:
                self._send_alert_email(alert, node)
            return alert

        if (pred and pred["status"] == "ok" and latest
                and latest["status"] in ("warning", "exceeded")
                and self.recovery_email_enabled):
            alert = self._insert_alert(node_id, pred, "recovered")
            if alert:
                self._send_recovery_email(alert, node)
            return alert

        return None

    def _insert_alert(self, node_id: int, pred: dict, status: str) -> dict:
        now = utc_now()
        projected = int(pred.get("projected_total_bytes", 0))
        threshold = int(pred.get("threshold_bytes", 0))
        avg_daily = int(pred.get("avg_daily_bytes", 0))
        days_left = int(pred.get("days_left", 0))
        exceed_day = None if status == "recovered" else pred.get("projected_exceed_day")
        detail = json.dumps(pred, ensure_ascii=False)
        with self.lock, self.db:
            cur = self.db.execute("""
              INSERT INTO alerts(node_id, ts, status, predicted_total_bytes, threshold_bytes,
                                 avg_daily_bytes, days_left, projected_exceed_day, detail)
              VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (node_id, now, status, projected, threshold, avg_daily, days_left, exceed_day, detail))
            return {
                "id": cur.lastrowid,
                "node_id": node_id,
                "status": status,
                "predicted_total_bytes": projected,
                "threshold_bytes": threshold,
                "avg_daily_bytes": avg_daily,
                "days_left": days_left,
                "projected_exceed_day": exceed_day,
            }

    def _cooldown_ok(self, node_id: int, status: str) -> bool:
        now = utc_now()
        key = (node_id, status)
        last = self._last_email.get(key)
        if last is not None and now - last < self.alert_cooldown_hours * 3600:
            return False
        self._last_email[key] = now
        return True

    def _send_email(self, subject: str, body: str, to_addrs=None) -> bool:
        smtp = self.smtp_config or {}
        if not smtp.get("enabled"):
            print(f"{APP_NAME}: [mail skip] {subject}（SMTP 未启用）", flush=True)
            return False
        try:
            send_email(smtp, subject, body, to_addrs)
            print(f"{APP_NAME}: mail sent: {subject}", flush=True)
            return True
        except Exception as exc:
            print(f"{APP_NAME}: mail error: {exc}", flush=True)
            return False

    def _current_total(self, node) -> int:
        return (node["last_rx_bytes"] or 0) + (node["last_tx_bytes"] or 0)

    def _send_alert_email(self, alert: dict, node) -> None:
        if not self._cooldown_ok(alert["node_id"], alert["status"]):
            return
        status_text = "已超限" if alert["status"] == "exceeded" else "预计超限"
        subject = f"[VPS流量监控] {node['name']} {status_text}提醒"
        body = (
            f"节点：{node['name']}（{node['host'] or '-'}）\n"
            f"状态：{status_text}\n"
            f"本周期已用：{format_bytes(self._current_total(node))}\n"
            f"近 7 日日均：{format_bytes(alert['avg_daily_bytes'])}\n"
            f"预计重置日前总量：{format_bytes(alert['predicted_total_bytes'])}\n"
            f"阈值：{format_bytes(alert['threshold_bytes'])}\n"
            f"剩余天数：{alert['days_left']}\n"
            f"预计超限日：{alert['projected_exceed_day'] or '-'}\n"
        )
        if self.dashboard_url:
            body += f"Dashboard：{self.dashboard_url}\n"
        self._send_email(subject, body, node["alert_email"] or None)

    def _send_recovery_email(self, alert: dict, node) -> None:
        subject = f"[VPS流量监控] {node['name']} 已恢复正常"
        body = (
            f"节点：{node['name']}（{node['host'] or '-'}）\n"
            f"状态：已恢复\n"
            f"本周期已用：{format_bytes(self._current_total(node))}\n"
            f"阈值：{format_bytes(alert['threshold_bytes'])}\n"
            f"剩余天数：{alert['days_left']}\n"
        )
        if self.dashboard_url:
            body += f"Dashboard：{self.dashboard_url}\n"
        self._send_email(subject, body, node["alert_email"] or None)

    def overview(self, admin: bool = False) -> dict:
        now = utc_now()
        nodes = []
        for node in self.list_nodes():
            daily = [dict(row) for row in self.daily_usage(node["id"], 7)]
            daily.reverse()
            rates = {"rx_bps": 0, "tx_bps": 0}
            interfaces = []
            latest = self.last_sample(node["id"])
            if latest and latest["raw_json"]:
                try:
                    raw = json.loads(latest["raw_json"])
                    rates = raw.get("rates") or rates
                    interfaces = raw.get("interfaces") or []
                except Exception:
                    pass
            last_seen = node["last_seen_ts"]
            online = bool(last_seen) and (now - last_seen <= ONLINE_THRESHOLD_SECONDS)
            current_rx = node["last_rx_bytes"] or 0
            current_tx = node["last_tx_bytes"] or 0
            item = {
                "id": node["id"],
                "name": node["name"],
                "host": node["host"],
                "online": online,
                "last_seen_ts": last_seen,
                "reset_day": node["reset_day"],
                "current_rx_bytes": current_rx,
                "current_tx_bytes": current_tx,
                "current_total_bytes": current_rx + current_tx,
                "rate_rx_bps": rates.get("rx_bps", 0),
                "rate_tx_bps": rates.get("tx_bps", 0),
                "interfaces": interfaces,
                "daily": daily,
                "prediction": self.compute_prediction(node),
            }
            if admin:
                item.update({
                    "threshold_bytes": node["threshold_bytes"],
                    "alert_email": node["alert_email"],
                    "token": node["token"] or "",
                })
            nodes.append(item)
        alerts = [dict(row) for row in self.recent_alerts(20)]
        return {"now": now, "nodes": nodes, "alerts": alerts, "admin": admin}


def make_handler(config_path: str, store: HubStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = APP_NAME

        def cfg(self) -> dict:
            return load_config(config_path)

        def send_json(self, status: int, data) -> None:
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

        def is_admin(self) -> bool:
            return verify_session(self.cfg(), self.cookie_value("vps_hub_session"))

        def require_admin(self) -> None:
            if not self.is_admin():
                raise PermissionError("login required")

        def hub_base_url(self) -> str:
            base = (self.cfg().get("hub_public_url") or "").strip()
            if base:
                return base.rstrip("/")
            return f"http://{self.headers.get('Host', '')}"

        def agent_command(self, token: str) -> str:
            hub = self.hub_base_url()
            return (f"bash <(curl -fsSL {REPO_RAW}/install.sh) --action install "
                    f"--hub-url '{hub}' --agent-token '{token}' --report-interval 60")

        def smtp_view(self) -> dict:
            smtp = self.cfg().get("smtp") or {}
            return {
                "enabled": bool(smtp.get("enabled")),
                "host": smtp.get("host", ""),
                "port": int(smtp.get("port") or 587),
                "username": smtp.get("username", ""),
                "from_addr": smtp.get("from_addr", ""),
                "to_addrs": smtp.get("to_addrs") or [],
                "use_ssl": bool(smtp.get("use_ssl")),
                "use_starttls": bool(smtp.get("use_starttls", True)),
            }

        def save_smtp(self, config: dict, data: dict) -> None:
            smtp = dict(config.get("smtp") or {})
            smtp["enabled"] = bool(data.get("enabled", smtp.get("enabled", False)))
            smtp["host"] = str(data.get("host", smtp.get("host", ""))).strip()
            smtp["port"] = int(data.get("port", smtp.get("port", 587)))
            smtp["username"] = str(data.get("username", smtp.get("username", ""))).strip()
            if str(data.get("password", "")):
                smtp["password"] = str(data["password"])
            smtp["from_addr"] = str(data.get("from_addr", smtp.get("from_addr", ""))).strip()
            if data.get("to_addrs") is not None:
                smtp["to_addrs"] = [x.strip() for x in str(data.get("to_addrs", "")).split(",") if x.strip()]
            smtp["use_ssl"] = bool(data.get("use_ssl", smtp.get("use_ssl", False)))
            smtp["use_starttls"] = bool(data.get("use_starttls", smtp.get("use_starttls", True)))
            config["smtp"] = smtp
            save_config(config_path, config)
            store.smtp_config = smtp

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.send_json(200, {"ok": True})
            elif parsed.path == "/api/status":
                admin = self.is_admin()
                data = store.overview(admin=admin)
                data["admin"] = admin
                if admin:
                    data["smtp"] = self.smtp_view()
                    data["hub_public_url"] = self.cfg().get("hub_public_url", "")
                self.send_json(200, data)
            elif parsed.path == "/":
                body = DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                data = self.read_json()
                config = self.cfg()
                if parsed.path == "/api/report":
                    result = store.ingest_report(data)
                    self.send_json(200, {"ok": True, **result})
                elif parsed.path == "/api/login":
                    username = str(data.get("username", ""))
                    password = str(data.get("password", ""))
                    if username != config.get("admin_user") or not verify_password(password, config.get("admin_password_hash", "")):
                        raise PermissionError("invalid username or password")
                    token = make_session(config, username)
                    body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", f"vps_hub_session={token}; Path=/; HttpOnly; SameSite=Lax")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/logout":
                    body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", "vps_hub_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/smtp":
                    self.require_admin()
                    self.save_smtp(config, data)
                    self.send_json(200, {"ok": True})
                elif parsed.path == "/api/test-email":
                    self.require_admin()
                    smtp = config.get("smtp") or {}
                    if not smtp.get("enabled"):
                        raise ValueError("SMTP 未启用")
                    send_email(smtp, "[VPS流量监控] 测试邮件", "这是一封测试邮件，说明 SMTP 配置可用。")
                    self.send_json(200, {"ok": True})
                elif parsed.path == "/api/nodes":
                    self.require_admin()
                    name = str(data.get("name", "")).strip()
                    token = store.add_node(
                        name,
                        host=str(data.get("host", "")).strip(),
                        reset_day=int(data.get("reset_day", 1) or 1),
                        threshold_bytes=int(float(data.get("threshold_gb", 0) or 0) * (1024 ** 3)),
                        alert_email=str(data.get("email", "")).strip(),
                    )
                    node = store.get_node_by_token(token)
                    self.send_json(200, {"ok": True, "node_id": node["id"], "token": token, "command": self.agent_command(token)})
                elif parsed.path.startswith("/api/nodes/"):
                    self.require_admin()
                    match = re.fullmatch(r"/api/nodes/(\d+)/(update|delete|token)", parsed.path)
                    if not match:
                        self.send_error(404)
                        return
                    node_id = int(match.group(1))
                    action = match.group(2)
                    if action == "update":
                        store.update_node(
                            node_id,
                            name=str(data.get("name", "")).strip() or None,
                            host=str(data.get("host", "")).strip(),
                            reset_day=int(data.get("reset_day") or 1) if data.get("reset_day") is not None else None,
                            threshold_bytes=int(float(data.get("threshold_gb") or 0) * (1024 ** 3)) if data.get("threshold_gb") is not None else None,
                            alert_email=str(data.get("email", "")).strip(),
                        )
                        self.send_json(200, {"ok": True})
                    elif action == "delete":
                        store.delete_node(node_id)
                        self.send_json(200, {"ok": True})
                    elif action == "token":
                        token = store.regenerate_token(node_id)
                        self.send_json(200, {"ok": True, "token": token, "command": self.agent_command(token)})
                else:
                    self.send_error(404)
            except PermissionError as exc:
                self.send_json(403, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def log_message(self, fmt, *args) -> None:
            print(f"{APP_NAME}: {self.address_string()} - {fmt % args}", flush=True)

    return Handler




def main() -> int:
    parser = argparse.ArgumentParser(description="VPS traffic hub service")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--add-node", metavar="NAME", help="新建节点并输出 agent token 后退出")
    parser.add_argument("--set-node", metavar="ID", type=int, help="更新指定节点的阈值/重置日/收件人后退出")
    parser.add_argument("--threshold-gb", type=float, default=None, help="流量阈值（GB），0 表示未启用")
    parser.add_argument("--reset-day", type=int, default=None, help="每月流量重置日 1-31")
    parser.add_argument("--email", default=None, help="该节点的告警收件邮箱（可覆盖全局）")
    parser.add_argument("--list-nodes", action="store_true", help="列出所有节点后退出")
    parser.add_argument("--test-email", action="store_true", help="用当前 SMTP 配置发送一封测试邮件后退出")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.test_email:
        smtp = config.get("smtp") or {}
        if not smtp.get("enabled"):
            print("SMTP 未启用：请在配置文件的 smtp 段设置 enabled=true 并填写 host/from_addr/to_addrs。")
            return 1
        try:
            send_email(smtp, "[VPS流量监控] 测试邮件", "这是一封测试邮件，说明 Hub 的 SMTP 配置可用。")
            print("测试邮件已发送。")
            return 0
        except Exception as exc:
            print(f"测试邮件发送失败: {exc}")
            return 1

    if args.add_node or args.set_node or args.list_nodes:
        store = HubStore(config["database"])
        try:
            if args.add_node:
                token = store.add_node(args.add_node)
                print(f"node={args.add_node}")
                print(f"agent_token={token}")
            if args.set_node:
                threshold_bytes = None
                if args.threshold_gb is not None:
                    threshold_bytes = int(max(0.0, args.threshold_gb) * (1024 ** 3))
                store.update_node(args.set_node, reset_day=args.reset_day,
                                  threshold_bytes=threshold_bytes, alert_email=args.email)
                node = store.get_node(args.set_node)
                print(f"updated id={node['id']} name={node['name']} reset_day={node['reset_day']} "
                      f"threshold_bytes={node['threshold_bytes']} email={node['alert_email']}")
            if args.list_nodes:
                for row in store.list_nodes():
                    print(f"id={row['id']} name={row['name']} host={row['host']} "
                          f"reset_day={row['reset_day']} threshold_bytes={row['threshold_bytes']} "
                          f"email={row['alert_email']} last_seen={row['last_seen_ts']}")
        finally:
            store.close()
        return 0

    save_config(args.config, config)
    store = HubStore(
        config["database"],
        smtp_config=config.get("smtp") or {},
        alert_cooldown_hours=config.get("alert_cooldown_hours", 24),
        recovery_email_enabled=config.get("recovery_email_enabled", True),
        dashboard_url=config.get("dashboard_url", ""),
    )
    httpd = ThreadingHTTPServer((config["host"], config["port"]), make_handler(args.config, store))

    def shutdown(signum, frame):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"{APP_NAME}: listening on {config['host']}:{config['port']}", flush=True)
    httpd.serve_forever()
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
