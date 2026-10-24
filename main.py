"""ddns6fw：把设备当前 IPv6 地址同步到锐捷路由器的防火墙（端口开放）规则。

运行方式（容器 host 网络模式）：
    python main.py            # 启动 Web 配置界面 + 后台循环同步
    python main.py --once     # 只同步一次后退出
    python main.py --no-web   # 不启动 Web 界面
"""

import argparse
import json
import logging
import os
import sys
import threading
import time

import config as config_module
import ipv6
import router

LOG = logging.getLogger("ddns6fw")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RULE_DEFAULTS = {
    "enable": "1",
    "netProto": "ipv4",
    "log": "0",
    "state": "1,4",
    "by": "mac",
    "mac": "",
    "src": "lan",
    "dest": "wan",
    "srcIP": "",
    "destIP": "",
    "srcPort": "",
    "destPort": "",
    "proto": "all",
    "target": "REJECT",
    "tmngtName": "默认时间",
    "ruleName": "",
    "time": {},
}


def match_rules(rules, ports):
    wanted = {str(p) for p in (ports or [])}
    matched = []
    for rule in rules:
        if rule.get("netProto") != "ipv6":
            continue
        if rule.get("src") != "wan" or rule.get("target") != "ACCEPT":
            continue
        if not rule.get("destIP"):
            continue
        if wanted and str(rule.get("destPort", "")) not in wanted:
            continue
        matched.append(rule)
    return matched


def build_payload(rule, new_ip):
    payload = dict(RULE_DEFAULTS)
    payload.update(rule)
    payload["destIP"] = new_ip
    return payload


def response_error(resp):
    if not isinstance(resp, dict):
        return "响应异常：%r" % (resp,)
    if resp.get("code") not in (0, None):
        return json.dumps(resp, ensure_ascii=False)
    data = resp.get("data")
    if isinstance(data, dict):
        rcode = data.get("rcode")
        if rcode is not None:
            rcode = str(rcode)
            if not (rcode.isdigit() and len(rcode) == 8 and rcode[4:] == "0000"):
                return data.get("message") or ("rcode=%s" % rcode)
    return None


class Syncer:
    def __init__(self, config_store, state_store, wake=None):
        self.config_store = config_store
        self.state_store = state_store
        self.wake = wake
        self._client = None
        self._client_key = None
        self._lock = threading.Lock()

    def _client_for(self, host, password, timeout):
        key = (host, password, timeout)
        if self._client is None or self._client_key != key:
            self._client = router.RouterClient(host, password, timeout=timeout)
            self._client_key = key
        return self._client

    def run_once(self):
        with self._lock:
            return self._run_once()

    def _run_once(self):
        cfg = self.config_store.get()
        result = {"time": time.time()}
        host = (cfg.get("router_host") or "").strip()
        password = cfg.get("router_password") or ""
        if not host or not password:
            return self._finish(result, ok=False,
                                error="尚未配置路由器地址或密码，请在网页中填写")

        try:
            client = self._client_for(host, password, cfg.get("timeout", 15))
            if not client.sid:
                client.login()
            rules = client.get_rules()
            targets = match_rules(rules, cfg.get("ports", []))
            result["matched"] = len(targets)

            # 参考地址：优先用上次成功写入的地址；否则用规则里现有的目标地址。
            # 这样即使“后缀”留空，也能在网段变化后选中接口标识相同的那个地址。
            reference = self.state_store.get().get("last_ipv6")
            if not reference:
                for rule in targets:
                    if rule.get("destIP"):
                        reference = rule["destIP"]
                        break

            entries = ipv6.list_system_ipv6()
            current = ipv6.choose_address(
                entries,
                interface=cfg.get("interface") or None,
                suffix=cfg.get("ipv6_suffix") or None,
                prefer_address=reference,
            )
            if not current:
                return self._finish(result, ok=False,
                                    error="未找到可用的全局 IPv6 地址（检查网卡名 / 后缀配置）")
            result["ipv6"] = current

            if not targets:
                return self._finish(result, ok=True, message="未匹配到需要更新的规则")
            updates = [build_payload(r, current) for r in targets if r.get("destIP") != current]
            if not updates:
                return self._finish(result, ok=True, message="规则已是最新，无需修改")
            resp = client.update_rules(updates)
            err = response_error(resp)
            if err:
                return self._finish(result, ok=False, error="更新规则失败：%s" % err)
            return self._finish(result, ok=True, message="已更新 %d 条规则" % len(updates),
                                updated=len(updates))
        except router.RouterError as exc:
            return self._finish(result, ok=False, error=str(exc))
        except Exception as exc:
            return self._finish(result, ok=False, error="异常：%r" % exc)

    def _finish(self, result, ok, error=None, message=None, updated=0):
        result["ok"] = ok
        result["updated"] = updated
        if error:
            result["error"] = error
        if message:
            result["message"] = message
        result["last_run"] = time.time()
        patch = {"last_result": result}
        if result.get("ipv6"):
            patch["last_ipv6"] = result["ipv6"]
        self.state_store.update(patch)
        if ok:
            LOG.info("同步成功：IPv6=%s %s", result.get("ipv6"), message or "")
        else:
            LOG.warning("同步失败：%s", error)
        return result


def main():
    parser = argparse.ArgumentParser(description="锐捷路由器 IPv6 防火墙规则 DDNS")
    parser.add_argument("--config", default=os.path.join(BASE_DIR, "config.json"))
    parser.add_argument("--state", default=os.path.join(BASE_DIR, "state.json"))
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--no-web", action="store_true", help="不启动 Web 配置界面")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    LOG.info("ddns6fw 启动：Python %s，配置=%s，状态=%s",
             sys.version.split()[0], args.config, args.state)

    store = config_module.ConfigStore(args.config)
    state = config_module.StateStore(args.state)
    wake = threading.Event()
    syncer = Syncer(store, state, wake)

    if args.once:
        result = syncer.run_once()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not args.no_web:
        import webui
        cfg = store.get()
        try:
            webui.start_server(store, state, syncer)
            LOG.info("Web 配置界面：http://%s:%s", cfg.get("web_host"), cfg.get("web_port"))
        except OSError as exc:
            LOG.warning("Web 界面启动失败（端口可能被占用）：%s", exc)

    LOG.info("主循环已启动，当前间隔 %s 秒", store.get().get("interval"))
    try:
        while True:
            cfg = store.get()
            if cfg.get("enabled", True):
                syncer.run_once()
            else:
                LOG.info("同步已禁用，跳过本轮")
            try:
                interval = max(10, int(cfg.get("interval", 60)))
            except (TypeError, ValueError):
                interval = 60
            wake.wait(interval)
            wake.clear()
    except KeyboardInterrupt:
        LOG.info("收到中断，进程退出")


if __name__ == "__main__":
    main()
