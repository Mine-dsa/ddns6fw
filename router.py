"""锐捷 Reyee 路由器客户端：登录并操作 access_ctrl 防火墙规则。"""

import json
import re
import ssl
import time
import urllib.error
import urllib.request

import gibberish

AES_KEY_RE = re.compile(r'GibberishAES\.enc\(passwordEl\.value,\s*"([0-9a-fA-F]+)"\)')


class RouterError(Exception):
    pass


class RouterClient:
    def __init__(self, host, password, timeout=15, verify_tls=False):
        self.host = host
        self.password = password
        self.timeout = timeout
        self.base = host if host.startswith("http") else "https://" + host
        self.sid = None
        self.token = None
        self.sn = None
        self._ctx = ssl.create_default_context()
        if not verify_tls:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _open(self, path, data=None, auth=False, method=None):
        url = self.base + path
        if auth and self.sid:
            url += ("&" if "?" in url else "?") + "auth=" + self.sid
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     method=method or ("POST" if body is not None else "GET"))
        req.add_header("User-Agent", "Mozilla/5.0")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self._ctx),
        )
        status = None
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                status = resp.getcode()
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise RouterError("无法连接路由器 %s：%s" % (self.host, exc.reason))
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed.setdefault("_http_status", status)
                return parsed
        except ValueError:
            pass
        return {"_http_status": status, "_raw": raw}

    def login(self):
        page = self._open("/cgi-bin/luci/")
        raw = page.get("_raw") if isinstance(page, dict) else None
        if not raw:
            raise RouterError("获取登录页失败：%s" % json.dumps(page, ensure_ascii=False))
        match = AES_KEY_RE.search(raw)
        if not match:
            raise RouterError("未能在登录页解析到 AES 密钥（固件版本可能不同）")
        key = match.group(1)
        pwd = gibberish.encrypt(self.password, key)
        body = {"method": "login", "params": {
            "username": "admin",
            "time": str(int(time.time())),
            "encry": True,
            "pwd": pwd,
        }}
        resp = self._open("/cgi-bin/luci/api/auth", body)
        data = resp.get("data") if isinstance(resp, dict) else None
        if not data or not data.get("sid"):
            hint = ""
            if isinstance(data, dict):
                if data.get("lockTime"):
                    hint = "（密码错误次数过多，请等待 %s 秒）" % data.get("lockTime")
                elif data.get("msg"):
                    hint = "（%s）" % data.get("msg")
            raise RouterError("登录失败%s：%s" % (hint, json.dumps(resp, ensure_ascii=False)))
        self.sid = data["sid"]
        self.token = data.get("token")
        self.sn = data.get("sn")
        return data

    @staticmethod
    def _auth_failed(resp):
        if not isinstance(resp, dict):
            return True
        if resp.get("_http_status") not in (None, 200):
            return True
        if "code" not in resp and "_raw" in resp:
            return True
        return resp.get("code") == 2

    def api_cmd(self, method, module, data=None, async_=None, no_parse=False, device="pc"):
        params = {
            "module": module,
            "noParse": no_parse,
            "async": async_,
            "remoteIp": False,
            "device": device,
        }
        if data is not None:
            params["data"] = data
        body = {"method": method, "params": params}
        resp = self._open("/cgi-bin/luci/api/cmd", body, auth=True)
        if self._auth_failed(resp):
            self.login()
            resp = self._open("/cgi-bin/luci/api/cmd", body, auth=True)
        return resp

    def get_rules(self):
        resp = self.api_cmd("devConfig.get", "access_ctrl")
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict):
            raise RouterError("读取规则失败：%s" % json.dumps(resp, ensure_ascii=False))
        return data.get("list") or []

    def update_rules(self, rules):
        return self.api_cmd("devConfig.update", "access_ctrl",
                            data={"list": rules}, async_=True)

    def add_rules(self, rules):
        return self.api_cmd("devConfig.add", "access_ctrl",
                            data={"list": rules}, async_=True)

    def delete_rules(self, uuids):
        if isinstance(uuids, str):
            uuids = [uuids]
        return self.api_cmd("devConfig.del", "access_ctrl",
                            data={"uuid": list(uuids)}, async_=True)
