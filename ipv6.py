"""从宿主机网卡读取全局 IPv6 地址（容器为 host 网络模式）。

Linux 下直接解析 /proc/net/if_inet6，无需任何第三方依赖，
也不需要容器内安装 iproute2。
"""

import ipaddress

DEFAULT_PROC_PATH = "/proc/net/if_inet6"

FLAG_TEMPORARY = 0x01
FLAG_DADFAILED = 0x08
FLAG_DEPRECATED = 0x20
FLAG_TENTATIVE = 0x40

_ULA = ipaddress.IPv6Network("fc00::/7")


def _format_addr(hex32):
    return str(ipaddress.IPv6Address(int(hex32, 16)))


def interface_id(addr):
    """取地址的低 64 位（接口标识），用于在前缀变化后识别同一块网卡地址。"""
    return int(ipaddress.IPv6Address(addr)) & ((1 << 64) - 1)


def parse_proc_if_inet6(text):
    entries = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        hexaddr, ifindex, plen, scope, flags, ifname = parts[:6]
        try:
            entries.append({
                "addr": _format_addr(hexaddr),
                "ifindex": int(ifindex, 16),
                "prefixlen": int(plen, 16),
                "scope": int(scope, 16),
                "flags": int(flags, 16),
                "interface": ifname,
            })
        except ValueError:
            continue
    return entries


def _basic_usable(entry):
    if entry.get("scope") != 0:
        return False
    try:
        ip = ipaddress.IPv6Address(entry["addr"])
    except (KeyError, ValueError):
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    if ip in _ULA:
        return False
    flags = entry.get("flags", 0)
    if flags & FLAG_DADFAILED or flags & FLAG_TENTATIVE:
        return False
    return True


def choose_address(entries, interface=None, suffix=None, exclude_temporary=True, prefer_address=None):
    base = []
    for entry in entries:
        if interface and entry.get("interface") != interface:
            continue
        if _basic_usable(entry):
            base.append(entry)
    if not base:
        return None

    if suffix:
        suffix = suffix.lower()
        if ":" in suffix:
            for entry in base:
                if entry["addr"].lower().endswith(suffix):
                    return entry["addr"]
        else:
            for entry in base:
                if entry["addr"].lower().endswith(":" + suffix):
                    return entry["addr"]

    if prefer_address:
        for entry in base:
            if entry["addr"].lower() == prefer_address.lower():
                return entry["addr"]
        try:
            wanted_iid = interface_id(prefer_address)
        except ValueError:
            wanted_iid = None
        if wanted_iid is not None:
            for entry in base:
                try:
                    if interface_id(entry["addr"]) == wanted_iid:
                        return entry["addr"]
                except ValueError:
                    continue

    for entry in base:
        if exclude_temporary and (entry.get("flags", 0) & FLAG_TEMPORARY):
            continue
        if entry.get("flags", 0) & FLAG_DEPRECATED:
            continue
        return entry["addr"]

    for entry in base:
        return entry["addr"]
    return None


def list_system_ipv6(path=DEFAULT_PROC_PATH):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return parse_proc_if_inet6(handle.read())
    except OSError:
        return []


def get_current_ipv6(interface=None, suffix=None, exclude_temporary=True,
                     prefer_address=None, path=DEFAULT_PROC_PATH):
    entries = list_system_ipv6(path)
    return choose_address(entries, interface=interface, suffix=suffix,
                          exclude_temporary=exclude_temporary,
                          prefer_address=prefer_address)
