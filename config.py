"""配置与状态持久化。"""

import json
import os
import threading
import time

DEFAULT_CONFIG = {
    "router_host": "192.168.1.1",
    "router_password": "",
    "interface": "",
    "ipv6_suffix": "::1001",
    "ports": ["80", "443"],
    "interval": 60,
    "enabled": True,
    "web_host": "0.0.0.0",
    "web_port": 6060,
    "timeout": 15,
}


def _merge(defaults, loaded):
    result = dict(defaults)
    if isinstance(loaded, dict):
        result.update(loaded)
    return result


def _atomic_replace(tmp, path, attempts=6, delay=0.15):
    """原子替换，带重试（Windows 上杀毒/索引可能短暂占用目标文件）。"""
    last = None
    for _ in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(delay)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last


class ConfigStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        with self._lock:
            need_save = False
            if os.path.isfile(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8-sig") as handle:
                        loaded = json.load(handle)
                    self._data = _merge(DEFAULT_CONFIG, loaded)
                except (OSError, ValueError):
                    self._data = dict(DEFAULT_CONFIG)
                    need_save = True
            else:
                self._data = dict(DEFAULT_CONFIG)
                need_save = True
            if need_save:
                try:
                    self._save_locked()
                except OSError:
                    pass
            return dict(self._data)

    def get(self):
        with self._lock:
            return dict(self._data)

    def update(self, patch):
        with self._lock:
            data = dict(self._data)
            for key, value in (patch or {}).items():
                if value is None:
                    continue
                data[key] = value
            self._data = data
            self._save_locked()
            return dict(self._data)

    def replace(self, new_data):
        with self._lock:
            self._data = _merge(DEFAULT_CONFIG, new_data)
            self._save_locked()
            return dict(self._data)

    def _save_locked(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, ensure_ascii=False, indent=2)
        _atomic_replace(tmp, self.path)


class StateStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._data = {}
        self.load()

    def load(self):
        with self._lock:
            if os.path.isfile(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8-sig") as handle:
                        self._data = json.load(handle)
                except (OSError, ValueError):
                    self._data = {}
            return dict(self._data)

    def get(self):
        with self._lock:
            return dict(self._data)

    def update(self, patch):
        with self._lock:
            data = dict(self._data)
            data.update(patch or {})
            self._data = data
            try:
                directory = os.path.dirname(os.path.abspath(self.path))
                if directory and not os.path.isdir(directory):
                    os.makedirs(directory, exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, ensure_ascii=False, indent=2)
                _atomic_replace(tmp, self.path)
            except OSError:
                pass
            return dict(self._data)
