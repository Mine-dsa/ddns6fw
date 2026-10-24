"""ddns6fw 自测脚本。

用法：
    python selftest.py                                   # 只做本地测试（不碰路由器）
    python selftest.py --router --host 192.168.1.1 --password 你的密码
    python selftest.py --router --write --host ... --password ...   # 额外做真实读写测试

--write 只会新增一条“已禁用”的临时规则，测试完立即删除，
并且任何情况下都会把 80/443 规则的 destIP 恢复成测试前的值。
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import gibberish  # noqa: E402
import ipv6  # noqa: E402
import main as m  # noqa: E402
import router  # noqa: E402
import webui  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    line = ("[PASS] " if cond else "[FAIL] ") + name
    if not cond and detail:
        line += "  -> " + str(detail)
    print(line)


def test_aes():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f"
                        "101112131415161718191a1b1c1d1e1f")
    block = bytes.fromhex("00112233445566778899aabbccddeeff")
    out = gibberish._encrypt_block(block, gibberish._expand_key(key)).hex()
    check("AES-256 匹配 FIPS-197 官方向量",
          out == "8ea2b7ca516745bfeafc49904b496089", out)

    try:
        import hashlib
        import base64
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        salt = bytes.fromhex("0102030405060708")
        got = gibberish.encrypt("example-password", "0123456789abcdef0123456789abcdef", salt=salt)

        def evp(pw, s, kl=32, il=16):
            d = b""
            p = b""
            while len(d) < kl + il:
                p = hashlib.md5(p + pw + s).digest()
                d += p
            return d[:kl], d[kl:kl + il]
        k, iv = evp(b"0123456789abcdef0123456789abcdef", salt)
        raw = b"example-password"
        raw += bytes([16 - len(raw) % 16]) * (16 - len(raw) % 16)
        e = Cipher(algorithms.AES(k), modes.CBC(iv)).encryptor()
        ref = base64.b64encode(b"Salted__" + salt + e.update(raw) + e.finalize()).decode()
        check("GibberishAES 与 cryptography 密文一致", got == ref, got)
    except ImportError:
        print("[SKIP] 未安装 cryptography，跳过对照（容器内无需该库）")


def test_ipv6():
    sample = ("20010db8000000010000000000001001 02 40 00 00 eth0\n"
              "fe800000000000000000000000000001 02 40 20 80 eth0\n"
              "20010db8000000011111222233334444 02 40 00 01 eth0\n"
              "fd000000000000000000000000000005 03 40 00 00 eth1\n")
    entries = ipv6.parse_proc_if_inet6(sample)
    check("解析 /proc/net/if_inet6（4 条）", len(entries) == 4, len(entries))
    check("全局地址格式化正确",
          entries[0]["addr"] == "2001:db8:0:1::1001", entries[0]["addr"])
    check("按后缀选择", ipv6.choose_address(entries, suffix="::1001")
          == "2001:db8:0:1::1001")
    check("默认排除临时地址（选到永久地址）",
          ipv6.choose_address(entries) == "2001:db8:0:1::1001")
    check("排除 ULA（eth1 -> None）",
          ipv6.choose_address(entries, interface="eth1") is None)

    changed = ipv6.parse_proc_if_inet6(
        "20010db8000000020000000000001001 02 40 00 00 eth0\n"
        "20010db800000002aaaabbbbccccdddd 02 40 00 01 eth0\n")
    check("前缀变化后按接口标识自动跟随",
          ipv6.choose_address(changed, prefer_address="2001:db8:0:1::1001")
          == "2001:db8:0:2::1001")
    check("后缀变化后回退到第一块可用全局地址",
          ipv6.choose_address(
              ipv6.parse_proc_if_inet6(
                  "20010db8000000020000000000002002 02 40 00 00 eth0\n"
                  "20010db800000002aaaabbbbccccdddd 02 40 00 01 eth0\n"),
              prefer_address="2001:db8:0:1::1001")
          == "2001:db8:0:2::2002")


def test_config():
    tmp = tempfile.mkdtemp(prefix="ddns6fw_cfg_")
    path = os.path.join(tmp, "config.json")
    store = config.ConfigStore(path)
    check("首次启动自动创建 config.json", os.path.isfile(path))
    store.update({"interval": 77, "ports": ["80", "443", "9000"],
                  "router_password": "placeholder-password", "enabled": False})
    reloaded = config.ConfigStore(path).get()
    check("配置跨实例持久化（interval/ports/密码）",
          reloaded["interval"] == 77 and reloaded["ports"] == ["80", "443", "9000"]
          and reloaded["router_password"] == "placeholder-password",
          reloaded)

    with open(path, "w", encoding="utf-8-sig") as handle:
        json.dump({"interval": 12}, handle)
    check("容忍带 BOM 的 config.json",
          config.ConfigStore(path).get()["interval"] == 12)


def test_webui():
    tmp = tempfile.mkdtemp(prefix="ddns6fw_web_")
    store = config.ConfigStore(os.path.join(tmp, "config.json"))
    state = config.StateStore(os.path.join(tmp, "state.json"))
    wake = threading.Event()
    syncer = m.Syncer(store, state, wake)
    store.update({"web_port": 16099})
    server = webui.start_server(store, state, syncer)
    time.sleep(0.3)
    base = "http://127.0.0.1:16099"

    def get(path):
        return urllib.request.urlopen(base + path, timeout=5).read().decode("utf-8")

    def post(path, obj):
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=5).read().decode("utf-8")

    try:
        html = get("/")
        check("Web 首页可访问且含界面逻辑",
              len(html) > 2000 and "formLoaded" in html and "reloadForm" in html)
        check("GET /api/config 返回 JSON", "router_host" in get("/api/config"))
        post("/api/config", {"interval": 33, "ports": "80,443", "enabled": True,
                             "router_host": "127.0.0.1", "router_password": "placeholder-password"})
        check("POST /api/config 生效", store.get()["interval"] == 33)
        check("保存配置后唤醒主循环", wake.is_set())
        check("空密码不覆盖已存密码", store.get()["router_password"] == "placeholder-password")
        check("POST /api/run 返回结果", '"ok"' in post("/api/run", {}))
        check("GET /api/status 返回状态", "status" in get("/api/status"))
    finally:
        server.shutdown()


def _client(host, password):
    c = router.RouterClient(host, password)
    c.login()
    return c


def test_router_read(host, password):
    try:
        c = _client(host, password)
    except Exception as exc:
        check("路由器登录", False, exc)
        return None
    check("路由器登录成功", c.sid is not None)
    rules = c.get_rules()
    check("读取 access_ctrl 规则", isinstance(rules, list) and len(rules) > 0, rules)
    c.sid = None
    try:
        rules2 = c.get_rules()
        check("sid 失效后自动重新登录", len(rules2) == len(rules))
    except Exception as exc:
        check("sid 失效后自动重新登录", False, exc)
    return rules


def test_router_write(host, password):
    c = _client(host, password)
    baseline = c.get_rules()
    base_uuids = sorted(r.get("uuid") for r in baseline)
    orig = next((r.get("destIP") for r in baseline if str(r.get("destPort")) == "80"), None)
    uid = None
    try:
        dummy = {"enable": "0", "netProto": "ipv6", "state": "0", "by": "ip",
                 "src": "wan", "dest": "all", "destIP": "2001:db8::1",
                 "destPort": "9999", "proto": "tcp", "target": "ACCEPT",
                 "tmngtName": "默认时间", "ruleName": "selftest"}
        resp = c.add_rules([dummy])
        rows = [r for r in c.get_rules() if str(r.get("destPort")) == "9999"]
        check("新增临时规则", len(rows) == 1 and m.response_error(resp) is None, resp)
        if rows:
            uid = rows[0]["uuid"]
            upd = m.build_payload(rows[0], "2001:db8::2")
            resp2 = c.update_rules([upd])
            after = [r for r in c.get_rules() if r.get("uuid") == uid]
            check("修改临时规则 destIP",
                  after and after[0].get("destIP") == "2001:db8::2"
                  and m.response_error(resp2) is None, after)
    finally:
        if uid:
            c.delete_rules([uid])
        time.sleep(1)
        final_uuids = sorted(r.get("uuid") for r in c.get_rules())
        check("删除临时规则并保持现有规则不变",
              final_uuids == base_uuids
              and not any(str(r.get("destPort")) == "9999" for r in c.get_rules()),
              final_uuids)

    if not orig:
        return
    tmp = tempfile.mkdtemp(prefix="ddns6fw_e2e_")
    store = config.ConfigStore(os.path.join(tmp, "c.json"))
    store.update({"router_host": host, "router_password": password,
                  "ports": ["80", "443"], "ipv6_suffix": "", "interface": ""})
    state = config.StateStore(os.path.join(tmp, "s.json"))
    syncer = m.Syncer(store, state)
    real_lister = ipv6.list_system_ipv6

    def destip_of_ports():
        return {str(r.get("destPort")): r.get("destIP") for r in c.get_rules()
                if str(r.get("destPort")) in ("80", "443")}

    try:
        new_addr = orig.rsplit(":", 1)[0].rsplit(":", 1)[0] + "::2002"
        ipv6.list_system_ipv6 = lambda path=None: [
            {"addr": new_addr, "ifindex": 2, "prefixlen": 64,
             "scope": 0, "flags": 0, "interface": "eth0"}]
        res = syncer.run_once()
        got = destip_of_ports()
        check("端到端：检测到变化并更新 80/443 规则",
              res.get("ok") and res.get("updated") == 2
              and got.get("80") == new_addr and got.get("443") == new_addr, got)
    finally:
        ipv6.list_system_ipv6 = real_lister
        fix = [dict(r, destIP=orig) for r in c.get_rules()
               if str(r.get("destPort")) in ("80", "443") and r.get("destIP") != orig]
        if fix:
            c.update_rules(fix)
            time.sleep(1)
        check("端到端测试后已恢复原始 destIP", destip_of_ports().get("80") == orig,
              destip_of_ports())


def main():
    parser = argparse.ArgumentParser(description="ddns6fw 自测")
    parser.add_argument("--router", action="store_true", help="测试真实路由器（只读）")
    parser.add_argument("--write", action="store_true", help="额外做真实读写测试（会临时增删一条禁用规则）")
    parser.add_argument("--host", default="192.168.1.1")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    print("=== 本地测试 ===")
    test_aes()
    test_ipv6()
    test_config()
    test_webui()

    if args.router or args.write:
        print("=== 路由器测试 ===")
        test_router_read(args.host, args.password)
        if args.write:
            test_router_write(args.host, args.password)

    total = len(RESULTS)
    passed = sum(1 for _, ok in RESULTS if ok)
    print("=== 汇总：%d/%d 通过 ===" % (passed, total))
    if passed != total:
        for name, ok in RESULTS:
            if not ok:
                print("  未通过:", name)
        sys.exit(1)


if __name__ == "__main__":
    main()
