"""杰峰云端报文加解密（2026-11 从小程序 app-service.js 逆向得到）。

算法来源（uni-app 小程序，module 0ce6）：

    // 报文密钥：tms 后半段 + secret，截前 16 个字符
    function m(e){ var t=e.length; if(t>16) e=e.substring(0,16);
                   else for(var n=0;n<16-t;n++) e+=n; return e; }
    function y(e,t){ var n=[], r=e.length;
                     r/2>0 && n.push(e.substring(r/2)); n.push(t);
                     return m(n.join("")) }

    // 加密：AES-ECB + PKCS7，密文十六进制大写
    CryptoJS.AES.encrypt(JSON, Utf8.parse(key), {mode: ECB, padding: Pkcs7})
        .ciphertext.toString().toUpperCase()

    // 请求体：{ "req": "<hex>" }
    // 响应体：同样的 hex，用同一把 key 解密
"""

from __future__ import annotations

import json
import string
from typing import Any

try:  # cryptography 是 HA 自带依赖
    from cryptography.hazmat.primitives import padding as _padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover - 仅在缺少依赖时
    Cipher = None  # type: ignore[assignment]

_KEY_LEN = 16
_HEX_CHARS = set(string.hexdigits)


def derive_key(time_millis: str, secret: str) -> str:
    """按 ``y(tms, secret)`` 规则算出报文密钥。

    ``time_millis`` 是 19 位（6 位计数器 + 13 位毫秒），取后半段与 secret
    拼接后截断/补齐到 16 个字符。
    """
    half = len(time_millis) // 2
    joined = time_millis[half:] + secret
    if len(joined) > _KEY_LEN:
        return joined[:_KEY_LEN]
    if len(joined) < _KEY_LEN:
        extra = "".join(str(i) for i in range(_KEY_LEN))[: _KEY_LEN - len(joined)]
        joined += extra
    return joined


def _cipher(key: str) -> "Cipher":
    if Cipher is None:  # pragma: no cover
        raise RuntimeError("cryptography 不可用")
    return Cipher(algorithms.AES(key.encode("iso-8859-1")), modes.ECB())


def encrypt_body(payload: Any, key: str) -> str:
    """把 dict 或字符串加密成大写 HEX（服务端要求裸 hex，不包 wrapper）。"""
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    raw = text.encode("utf-8")
    padder = _padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    enc = _cipher(key).encryptor()
    cipher = enc.update(padded) + enc.finalize()
    return cipher.hex().upper()


def decrypt_body(text: str, key: str) -> Any:
    """解密响应 hex；不是 hex 或解密失败时退回原样（服务端偶尔返回明文）。"""
    cleaned = (text or "").strip()
    if len(cleaned) < 32 or len(cleaned) % 32 or any(c not in _HEX_CHARS for c in cleaned):
        return None
    try:
        dec = _cipher(key).decryptor()
        raw = dec.update(bytes.fromhex(cleaned)) + dec.finalize()
        unpadder = _padding.PKCS7(128).unpadder()
        plain = unpadder.update(raw) + unpadder.finalize()
        return json.loads(plain.decode("utf-8"))
    except Exception:
        return None


def is_ciphertext(text: str) -> bool:
    cleaned = (text or "").strip()
    return (
        len(cleaned) >= 32
        and len(cleaned) % 32 == 0
        and all(c in _HEX_CHARS for c in cleaned)
    )
