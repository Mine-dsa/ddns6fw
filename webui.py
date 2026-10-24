"""基于标准库 http.server 的简洁 Web 配置界面。"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ddns6fw 配置</title>
<style>
  body { font-family: "Microsoft YaHei", Arial, sans-serif; margin: 0; background: #f4f6f9; color: #222; }
  .wrap { max-width: 760px; margin: 24px auto; padding: 0 16px; }
  h1 { font-size: 20px; }
  .card { background: #fff; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  label { display: block; font-size: 13px; color: #555; margin: 10px 0 4px; }
  input[type=text], input[type=password], input[type=number] { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #ccd2da; border-radius: 6px; font-size: 14px; }
  .row { display: flex; gap: 14px; }
  .row > div { flex: 1; }
  button { margin-top: 16px; background: #255cdc; color: #fff; border: 0; border-radius: 6px; padding: 9px 18px; font-size: 14px; cursor: pointer; }
  button.ghost { background: #eef1f6; color: #255cdc; margin-left: 8px; }
  .status { font-size: 14px; line-height: 1.9; }
  .ok { color: #1a8f4a; }
  .err { color: #d2372b; }
  code { background: #f0f2f5; padding: 1px 5px; border-radius: 4px; }
  .hint { font-size: 12px; color: #888; margin-top: 4px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>ddns6fw · 锐捷 IPv6 防火墙规则同步</h1>

  <div class="card">
    <div class="status" id="status">正在读取状态…</div>
    <button class="ghost" onclick="runNow()">立即执行一次</button>
  </div>

  <div class="card">
    <div class="row">
      <div>
        <label>路由器地址</label>
        <input type="text" id="router_host" placeholder="192.168.1.1">
      </div>
      <div>
        <label>登录密码</label>
        <input type="password" id="router_password" placeholder="留空表示不修改">
      </div>
    </div>
    <div class="row">
      <div>
        <label>网卡名（可选）</label>
        <input type="text" id="interface" placeholder="例如 eth0，留空自动选择">
      </div>
      <div>
        <label>IPv6 后缀（可选）</label>
        <input type="text" id="ipv6_suffix" placeholder="例如 ::1001">
      </div>
    </div>
    <div class="row">
      <div>
        <label>开放的端口（逗号分隔）</label>
        <input type="text" id="ports" placeholder="80,443">
      </div>
      <div>
        <label>查询间隔（秒）</label>
        <input type="number" id="interval" min="10" placeholder="60">
      </div>
    </div>
    <div class="row">
      <div>
        <label>Web 端口（重启后生效）</label>
        <input type="number" id="web_port" min="1" placeholder="6060">
      </div>
      <div>
        <label>启用同步</label>
        <select id="enabled" style="width:100%;padding:8px 10px;border:1px solid #ccd2da;border-radius:6px;font-size:14px;">
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
      </div>
    </div>
    <button onclick="save()">保存配置</button>
    <button class="ghost" onclick="reloadForm()">重新载入</button>
    <span id="msg" class="hint" style="margin-left:10px"></span>
    <div class="hint">所有配置都会写入 config.json，下次启动自动读取，无需重复配置。匹配规则条件：IPv6 + 源 wan + 动作 ACCEPT + 目标地址非空 + 目标端口在列表中。</div>
  </div>
</div>

<script>
function q(id){ return document.getElementById(id); }
function fmt(t){ if(!t) return "-"; var d=new Date(t*1000); return d.toLocaleString(); }

function fill(cfg){
  q('router_host').value = cfg.router_host || '';
  q('interface').value = cfg.interface || '';
  q('ipv6_suffix').value = cfg.ipv6_suffix || '';
  q('ports').value = (cfg.ports || []).join(',');
  q('interval').value = cfg.interval != null ? cfg.interval : 60;
  q('web_port').value = cfg.web_port != null ? cfg.web_port : 6060;
  q('enabled').value = cfg.enabled ? 'true' : 'false';
}

function render(st){
  var s = st.status || {};
  var cls = s.ok ? 'ok' : 'err';
  var lines = [];
  lines.push('当前 IPv6：<code>' + (s.ipv6 || '未获取') + '</code>');
  lines.push('匹配规则数：' + (s.matched != null ? s.matched : '-') + '，本次更新：' + (s.updated != null ? s.updated : '-'));
  lines.push('最近结果：<span class="' + cls + '">' + (s.ok ? (s.message || '成功') : (s.error || '未知')) + '</span>');
  lines.push('最近执行：' + fmt(s.last_run));
  q('status').innerHTML = lines.join('<br>');
}

var formLoaded = false;

function load(){
  fetch('/api/status').then(function(r){ return r.json(); }).then(function(st){
    if(!formLoaded){ fill(st.config); formLoaded = true; }
    render(st);
  }).catch(function(){ q('status').textContent='读取失败'; });
}

function reloadForm(){
  formLoaded = false;
  q('msg').textContent = '';
  load();
}

function save(){
  var body = {
    router_host: q('router_host').value.trim(),
    router_password: q('router_password').value,
    interface: q('interface').value.trim(),
    ipv6_suffix: q('ipv6_suffix').value.trim(),
    ports: q('ports').value,
    interval: parseInt(q('interval').value || '60', 10),
    web_port: parseInt(q('web_port').value || '6060', 10),
    enabled: q('enabled').value === 'true'
  };
  fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)})
    .then(function(r){ return r.json(); }).then(function(res){
      q('router_password').value = '';
      if(res && res.ok && res.config){ fill(res.config); formLoaded = true; }
      q('msg').textContent = '已保存到 config.json';
      load();
    });
}

function runNow(){
  q('status').textContent = '正在执行…';
  fetch('/api/run', {method:'POST'}).then(function(r){ return r.json(); }).then(function(){ load(); });
}

load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    store = None
    state = None
    syncer = None

    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            self._json(self._status())
        elif path == "/api/config":
            self._json(self.store.get())
        else:
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            self._json({"ok": False, "error": "JSON 解析失败"}, 400)
            return
        if path == "/api/config":
            self.store.update(self._sanitize(payload))
            wake = getattr(self.syncer, "wake", None)
            if wake is not None:
                wake.set()
            self._json({"ok": True, "config": self.store.get()})
        elif path == "/api/run":
            self._json({"ok": True, "result": self.syncer.run_once()})
        else:
            self._json({"ok": False, "error": "未知接口"}, 404)

    @staticmethod
    def _sanitize(payload):
        allowed = {"router_host", "router_password", "interface", "ipv6_suffix",
                   "ports", "interval", "enabled", "web_port", "timeout"}
        out = {}
        for key, value in (payload or {}).items():
            if key not in allowed:
                continue
            if key == "router_password":
                if value:
                    out[key] = value
                continue
            if key in ("interval", "web_port", "timeout"):
                try:
                    out[key] = int(value)
                except (TypeError, ValueError):
                    continue
                continue
            if key == "ports":
                if isinstance(value, str):
                    value = [p.strip() for p in value.split(",") if p.strip()]
                elif isinstance(value, list):
                    value = [str(p).strip() for p in value if str(p).strip()]
                else:
                    continue
                out[key] = value
                continue
            if key == "enabled":
                out[key] = bool(value)
                continue
            out[key] = value
        return out

    def _status(self):
        stored = self.state.get()
        return {
            "config": self.store.get(),
            "status": stored.get("last_result") or {},
            "now": time.time(),
        }


def start_server(store, state, syncer):
    handler = type("BoundHandler", (_Handler,), {
        "store": store,
        "state": state,
        "syncer": syncer,
    })
    cfg = store.get()
    httpd = ThreadingHTTPServer(
        (cfg.get("web_host", "0.0.0.0"), int(cfg.get("web_port", 6060))), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
