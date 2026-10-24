"""GibberishAES 兼容的 OpenSSL AES-256-CBC 加密（纯标准库实现）。

锐捷 Reyee 路由器登录接口使用 GibberishAES.enc(password, passphrase)，
其格式与 `openssl enc -aes-256-cbc -salt -md md5` 完全一致：
    base64("Salted__" + salt(8) + ciphertext)
密钥由 EVP_BytesToKey(passphrase, salt, MD5, 迭代 1 次) 派生。
"""

import base64
import hashlib
import os


def _xtime(a):
    a &= 0xFF
    r = (a << 1) & 0xFF
    if a & 0x80:
        r ^= 0x1B
    return r


def _mul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p & 0xFF


def _gf_pow(a, n):
    r = 1
    for _ in range(n):
        r = _mul(r, a)
    return r


def _rotl8(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _build_sbox():
    inv = [0] * 256
    for i in range(1, 256):
        inv[i] = _gf_pow(i, 254)
    sbox = [0] * 256
    for i in range(256):
        x = inv[i]
        s = x ^ _rotl8(x, 1) ^ _rotl8(x, 2) ^ _rotl8(x, 3) ^ _rotl8(x, 4) ^ 0x63
        sbox[i] = s & 0xFF
    return sbox


SBOX = _build_sbox()
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key):
    nk = 8
    nr = 14
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon_idx = 0
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[rcon_idx]
            rcon_idx += 1
        elif i % nk == 4:
            temp = [SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return w


def _add_round_key(state, w, rnd):
    for c in range(4):
        word = w[rnd * 4 + c]
        for r in range(4):
            state[r][c] ^= word[r]


def _sub_bytes(state):
    for r in range(4):
        for c in range(4):
            state[r][c] = SBOX[state[r][c]]


def _shift_rows(state):
    for r in range(1, 4):
        state[r] = state[r][r:] + state[r][:r]


def _mix_columns(state):
    for c in range(4):
        a0 = state[0][c]
        a1 = state[1][c]
        a2 = state[2][c]
        a3 = state[3][c]
        t = a0 ^ a1 ^ a2 ^ a3
        state[0][c] = a0 ^ t ^ _xtime(a0 ^ a1)
        state[1][c] = a1 ^ t ^ _xtime(a1 ^ a2)
        state[2][c] = a2 ^ t ^ _xtime(a2 ^ a3)
        state[3][c] = a3 ^ t ^ _xtime(a3 ^ a0)


def _encrypt_block(block, w):
    state = [[block[4 * c + r] for c in range(4)] for r in range(4)]
    _add_round_key(state, w, 0)
    for rnd in range(1, 14):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, w, rnd)
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, w, 14)
    return bytes(state[r][c] for c in range(4) for r in range(4))


def _pkcs7_pad(data):
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def evp_bytes_to_key(passphrase, salt, key_len=32, iv_len=16):
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    material = b""
    prev = b""
    while len(material) < key_len + iv_len:
        prev = hashlib.md5(prev + passphrase + salt).digest()
        material += prev
    return material[:key_len], material[key_len:key_len + iv_len]


def aes256_cbc_encrypt(key, iv, data):
    w = _expand_key(key)
    out = b""
    prev = iv
    for i in range(0, len(data), 16):
        block = bytes(x ^ y for x, y in zip(data[i:i + 16], prev))
        enc = _encrypt_block(block, w)
        out += enc
        prev = enc
    return out


def encrypt(data, passphrase, salt=None):
    if salt is None:
        salt = os.urandom(8)
    text = data.encode("utf-8") if isinstance(data, str) else data
    key, iv = evp_bytes_to_key(passphrase, salt)
    ciphertext = aes256_cbc_encrypt(key, iv, _pkcs7_pad(text))
    return base64.b64encode(b"Salted__" + salt + ciphertext).decode("ascii")
