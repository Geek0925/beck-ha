"""Async client for the 杰峰 (JFTech / 博克 iCSee Home) cloud API.

两种鉴权模式：

1. **账号模式**（默认，App 的做法）
   ``POST /mobilephone/login/v3/{timeMillis}/{sign}.rs`` 拿到 token，
   之后每个请求带 ``Authorization`` + ``token`` 头。

2. **开放平台模式**
   直接用 uuid + appKey + appSecret 签名调用（open.jftech.com 申请）。

协议格式（逆向自 base.apk，com.jf.jlinkiot 2.5.1）::

    https://<host>/<接口代号>/<接口版本>/<timeMillis>/<signature>.rs
    headers: uuid=<uuid>, appKey=<appKey>

主机（APK 内出现过的全部相关域名）::

    rs.jftechws.com / rs.xmeye.net   账号 + 资源（登录在这里）
    api.jftechws.com                 REST 主入口
    jfcbss-api.xmcsrv.com            账号中心 CBS
    caps-cn.jftechws.com             云端穿透（P2P 视频）
    jvss.jftechws.com                视频流
    jfmq-v2.xmcsrv.net               MQTT（设备状态推送）
    ais.jftechws.com                 AI 服务

账号接口族（从 dex 字符串池提取）::

    /mobilephone/login/v3/{timeMillis}/{sign}.rs    手机号+密码登录
    /loginExist/v3/{timeMillis}/{sign}.rs           账号是否存在
    /userinfo2/v3/{timeMillis}/{secret}.rs          用户信息
    /expireTime/v3/{timeMillis}/{sign}.rs           token 过期时间
    /userregPhone|userregMail/v3/...                注册
    /user/resetpassword                             重置密码

设备/门锁接口族::

    /v3/device/getUserDeviceListByPage     设备列表
    /v3/device/getDeviceToken              设备令牌
    /v3/status/getDevicePropStatus         物模型属性（锁状态/电量/事件）
    /v3/access/send/command                下发设备指令
    /v3/deviceOpnLog/getRecordReport       开门记录
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid as uuid_module
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

try:  # Home Assistant always ships aiohttp; keep the package importable for tests
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

from .crypto import decrypt_body, derive_key, encrypt_body, is_ciphertext
from .defaults import APP_KEY as _APP_KEY, APP_SECRET as _APP_SECRET, APP_UUID as _APP_UUID, MOVE_CARD as _MOVE_CARD
from .errors import JFCloudAuthError, JFCloudConnectionError, JFCloudError
from .signature import SignatureGenerator

__all__ = [
    "DATA_HOST",
    "ENDPOINTS",
    "HOSTS",
    "JFCloudClient",
    "LOGIN_ENDPOINTS",
    "LOGIN_HOSTS",
    "OPEN_API_HOST",
]

HOSTS = {
    "cn": "cn-jvss.jftechws.com",
    "intl": "api.jftechws.com",
}

#: hosts tried in order when logging in with an account.
#: cn-ams is the account service the app actually uses (verified on-device).
LOGIN_HOSTS: tuple[str, ...] = (
    "cn-ams.jftechws.com",
    "rs.jftechws.com",
    "jfcbss-api.xmcsrv.com",
    "api.jftechws.com",
)

#: device/data service (all /v3/... calls)
DATA_HOST = "cn-jvss.jftechws.com"

#: open api gateway
OPEN_API_HOST = "api-cn.jftechws.com"
# 会话密钥（publicKey）握手用的 BOSS 网关
OPEN_API_BOSS = "boss-api.jftechsoft.com"

#: account-login endpoints, in the order they are attempted
LOGIN_ENDPOINTS: tuple[str, ...] = (
    "loginExist",
    "mobilephone/login",
    "login",
)

ENDPOINTS = {
    "device_list": ("GET", "v3/device/getUserDeviceListByPage"),
    "device_by_page": ("GET", "v3/device/getDeviceListByPage"),
    "device_token": ("POST", "v3/device/getDeviceToken"),
    "prop_status": ("POST", "v3/status/getDevicePropStatus"),
    "send_command": ("POST", "v3/access/send/command"),
    "record_report": ("POST", "v3/deviceOpnLog/getRecordReport"),
    "record_by_day": ("POST", "v3/deviceOpnLog/getPropStatusRecordByDay"),
    "device_timer": ("GET", "v3/deviceTimer/selectTimerByPage"),
    "device_share": ("GET", "v3/deviceShare/getSharedDeviceList"),
    "open_platform_token": ("POST", "gwp/v3/rtc/device/token"),
    "login_exist": ("POST", "loginExist"),
    "notify_log": ("POST", "v3/message/getNotifyLogList"),
}

#: 2xxx is the vendor's success range (2000 == OK).
_SUCCESS_CODES = range(2000, 3000)

_TOKEN_KEYS = ("accessToken", "token", "userToken", "jwt", "sessionToken")
#: error codes that mean "session expired, please sign in again"
_REAUTH_CODES = (401, 40103, 40001, 40002, 1001)

# 明文接口族：账号/登录/存在性检查走明文，其余（/v3/ 数据接口）走 AES-ECB 密文
_PLAINTEXT_PREFIXES = (
    "mobilephone/",
    "loginExist/",
    "userinfo",
    "account/",
    "userreg",
    "user/resetpassword",
    "expireTime/",
)


def _needs_encryption(code: str) -> bool:
    cleaned = code.lstrip("/")
    return not cleaned.startswith(_PLAINTEXT_PREFIXES)


def _parse_payload(text: str, key: str):
    """响应可能是明文 JSON，也可能是 AES-ECB 十六进制密文。"""
    import json as _json

    stripped = (text or "").strip()
    if not stripped:
        return {}
    try:
        return _json.loads(stripped)
    except ValueError:
        pass
    if is_ciphertext(stripped):
        decoded = decrypt_body(stripped, key) if key else None
        if decoded is not None:
            return decoded
        return {"code": 5009, "msg": "AES_ERROR: 无法解密响应（secret 不对）"}
    return {"code": -1, "msg": stripped[:200]}



async def _urllib_post(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> str:
    """用 urllib 重发同样的请求（实测该网关对某些 HTTP 客户端返回 500）。"""
    import asyncio as _asyncio
    import ssl as _ssl
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    def _call() -> str:
        req = _urlreq.Request(url, data=payload, headers=headers, method="POST")
        ctx = _ssl.create_default_context()
        try:
            with _urlreq.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode("utf-8", "replace")
        except _urlerr.HTTPError as err:
            return err.read().decode("utf-8", "replace")

    return await _asyncio.get_running_loop().run_in_executor(None, _call)


async def _urllib_request(
    url: str, *, method: str, payload: bytes | None, headers: dict[str, str], timeout: float
) -> str:
    """用 urllib 发请求（HA 的 aiohttp 会被该网关以 500 拒绝）。"""
    import asyncio as _asyncio
    import ssl as _ssl
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    def _call() -> str:
        req = _urlreq.Request(url, data=payload, headers=headers, method=method.upper())
        ctx = _ssl.create_default_context()
        try:
            with _urlreq.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode("utf-8", "replace")
        except _urlerr.HTTPError as err:
            return err.read().decode("utf-8", "replace")

    return await _asyncio.get_running_loop().run_in_executor(None, _call)


class JFCloudClient:
    """Signed REST client with account login or open-platform credentials."""

    def __init__(
        self,
        uuid: str = _APP_UUID,
        app_key: str = _APP_KEY,
        app_secret: str = _APP_SECRET,
        *,
        host: str | None = None,
        moved_card: int = _MOVE_CARD,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 20.0,
        login_name: str = "",
        password: str = "",
        area_code: str = "86",
        password_encoding: str = "md5",
        login_hosts: Iterable[str] | None = None,
    ) -> None:
        self.uuid = uuid or uuid_module.uuid4().hex
        self.app_key = app_key
        self.app_secret = app_secret
        self.host = host or HOSTS["cn"]
        self.timeout = timeout
        self.moved_card = moved_card
        self._signer = SignatureGenerator(self.uuid, app_key, app_secret, moved_card)
        self._session = session
        self._own_session = session is None

        # account mode
        self.login_name = login_name
        self.password = password
        self.area_code = area_code
        self.password_encoding = password_encoding
        self.login_hosts = tuple(login_hosts) if login_hosts else LOGIN_HOSTS
        self.token: str | None = None
        self.login_host: str | None = None
        # 报文加解密用的 secret（小程序里是登录后下发的 publicKey；先用 appSecret）
        self.encrypt_secret = app_secret
        self.public_key: str | None = None

    # ---------------------------------------------------------------- plumbing
    async def __aenter__(self) -> "JFCloudClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if aiohttp is None:  # pragma: no cover
            raise JFCloudConnectionError("aiohttp is required for network calls")
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _sign_pair(self) -> tuple[str, str]:
        return self._signer.next()

    def _build_url(self, code: str, time_millis: str, signature: str, host: str | None = None) -> str:
        # 账号类接口走 .rs 后缀；/v3 数据接口不带后缀（带 .rs 会命中需要 UniqueCode 的老网关）
        suffix = ".rs" if code.startswith(_PLAINTEXT_PREFIXES) else ""
        return f"https://{host or self.host}/{code}/{time_millis}/{signature}{suffix}"

    def _headers(self, time_millis: str | None = None, signature: str | None = None) -> dict[str, str]:
        # 必须与实测通过的组合完全一致：网关对这几个头敏感
        headers = {
            "uuid": self.uuid,
            "appKey": self.app_key,
            "Content-type": "application/json; charset=UTF-8",
            "Accept-Language": "zh",
            # 云端对 gzip 协商有兼容问题（aiohttp 默认带 gzip，实测会 500）
            "Accept-Encoding": "identity",
        }
        # api.jftechws.com 那套 Spring 接口要求把时间戳与签名同时放在 header 里
        if time_millis:
            headers["timeMillis"] = time_millis
        if signature:
            headers["signature"] = signature
        if self.token:
            headers["Authorization"] = self.token
            headers["token"] = self.token
        return headers

    async def _request_once(
        self,
        code: str,
        *,
        method: str,
        body: Any,
        params: Mapping[str, Any] | None,
        host: str | None = None,
    ) -> dict[str, Any]:
        """一次签名调用：统一走 urllib（该网关对 aiohttp 会返回 500）。"""
        time_millis, signature = self._sign_pair()
        url = self._build_url(code.lstrip("/"), time_millis, signature, host)
        if params:
            url = f"{url}?{urlencode(params)}"
        payload: bytes | None = None
        key = ""
        if body is not None and _needs_encryption(code) and self.encrypt_secret:
            key = derive_key(time_millis, self.encrypt_secret)
            payload = encrypt_body(body, key).encode("ascii")
        elif body is not None:
            import json as _json

            payload = _json.dumps(body).encode("utf-8")
        text = await _urllib_request(
            url,
            method=method,
            payload=payload,
            headers=self._headers(time_millis, signature),
            timeout=self.timeout,
        )
        return _parse_payload(text, key)

    async def raw_request(
        self,
        code: str,
        *,
        method: str = "POST",
        body: Any = None,
        params: Mapping[str, Any] | None = None,
        host: str | None = None,
        relogin: bool = True,
    ) -> dict[str, Any]:
        """Perform one signed call. ``code`` is a path like ``v3/status/getDevicePropStatus``."""
        try:
            payload = await self._request_once(
                code, method=method, body=body, params=params, host=host
            )
        except JFCloudAuthError:
            if not (relogin and self._can_reauth()):
                raise
            await self.login()
            payload = await self._request_once(
                code, method=method, body=body, params=params, host=host
            )
        code_value = payload.get("code", payload.get("ret"))
        if (
            relogin
            and isinstance(code_value, int)
            and code_value not in _SUCCESS_CODES
            and code_value in _REAUTH_CODES
            and self._can_reauth()
        ):
            await self.login()
            payload = await self._request_once(
                code, method=method, body=body, params=params, host=host
            )
            code_value = payload.get("code", payload.get("ret"))

        if isinstance(code_value, int) and code_value not in _SUCCESS_CODES:
            message = payload.get("msg") or payload.get("message") or "cloud error"
            if isinstance(message, str) and message.startswith(("NO_APP", "%7B")):
                raise JFCloudAuthError(
                    "appKey 未被云端认可（NO_APP / 5001）：需要 App 真实注册的 appKey",
                    code=code_value,
                    payload=payload,
                )
            raise JFCloudError(message, code=code_value, payload=payload)
        return payload

    def _can_reauth(self) -> bool:
        return bool(self.login_name and self.password)

    # ------------------------------------------------------------------ login
    def _password_payload(self) -> str:
        if self.password_encoding == "plain":
            return self.password
        return hashlib.md5(self.password.encode("utf-8")).hexdigest()

    def _login_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "loginName": self.login_name,
            "password": self._password_payload(),
        }
        # areaCode only makes sense for phone numbers, not e-mail accounts
        if self.area_code and self.login_name.isdigit():
            body["areaCode"] = self.area_code
        return body

    @staticmethod
    def _extract_token(payload: Mapping[str, Any]) -> str | None:
        data = payload.get("data")
        candidates: list[Mapping[str, Any]] = [payload]
        if isinstance(data, Mapping):
            candidates.insert(0, data)
        for source in candidates:
            for key in _TOKEN_KEYS:
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    async def login(
        self,
        login_name: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Sign in and keep the session token.

        Tries the known account hosts and login endpoints in order, then the
        swapped appKey/appSecret pair, so a DNS, region or key-order mismatch
        self-heals instead of needing a config change.
        """
        if login_name:
            self.login_name = login_name
        if password:
            self.password = password
        if not (self.login_name and self.password):
            raise JFCloudAuthError("account login needs a login name and a password")

        pairs: list[tuple[str, str]] = [(self.app_key, self.app_secret)]
        if self.app_key and self.app_secret and self.app_key != self.app_secret:
            pairs.append((self.app_secret, self.app_key))

        last_error: Exception | None = None
        for app_key, app_secret in pairs:
            self._signer = SignatureGenerator(self.uuid, app_key, app_secret, self.moved_card)
            for host in self.login_hosts:
                for endpoint in LOGIN_ENDPOINTS:
                    code = f"{endpoint}/v3"
                    try:
                        payload = await self._form_login(code, host)
                    except (JFCloudConnectionError, JFCloudAuthError) as err:
                        last_error = err
                        continue
                    if payload is None:
                        continue

                    code_value = payload.get("code", payload.get("ret"))
                    token = self._extract_token(payload)
                    if token and (
                        not isinstance(code_value, int) or code_value in _SUCCESS_CODES
                    ):
                        self.token = token
                        self.login_host = host
                        self.app_key, self.app_secret = app_key, app_secret
                        data = payload.get("data")
                        if isinstance(data, Mapping):
                            returned_uuid = data.get("uuid")
                            if isinstance(returned_uuid, str) and returned_uuid:
                                self.uuid = returned_uuid
                                self._signer.uuid = returned_uuid
                        await self._exchange_public_key()
                        return payload
                    last_error = JFCloudError(
                        payload.get("msg") or "login rejected",
                        code=code_value if isinstance(code_value, int) else None,
                        payload=payload,
                    )
        raise last_error or JFCloudAuthError("login failed on every known endpoint")

    async def _form_login(self, code: str, host: str) -> dict[str, Any] | None:
        """表单登录：body 是 ``account=..&pass=..`` 的 AES 密文（实测可用）。"""
        import json as _json
        from urllib.parse import quote

        time_millis, signature = self._sign_pair()
        key = derive_key(time_millis, self.encrypt_secret or self.app_secret)
        form = f"account={quote(self.login_name)}&pass={quote(self.password)}"
        headers = self._headers(time_millis, signature)
        headers["Content-type"] = "application/x-www-form-urlencoded"
        headers["Authorization"] = ""
        url = f"https://{host}/{code}/{time_millis}/{signature}.rs"
        try:
            text = await _urllib_request(
                url,
                method="POST",
                payload=encrypt_body(form, key).encode("ascii"),
                headers=headers,
                timeout=self.timeout,
            )
        except Exception as err:  # noqa: BLE001
            raise JFCloudConnectionError(str(err)) from err
        plain = decrypt_body(text.strip(), key) if is_ciphertext(text.strip()) else None
        if isinstance(plain, Mapping):
            return dict(plain)
        if isinstance(plain, str) and plain:
            return _json.loads(plain)
        return None

    async def _exchange_public_key(self) -> None:
        """用 accessToken 换会话密钥 publicKey（小程序 handleLogin 的做法）。

        POST boss-api.jftechsoft.com/account/userToken/login
        body: {"request": {"appKey": ..., "authorization": <token>}}
        resp: {"code": 2000, "data": {"publicKey": "...", "accessToken": "..."}}

        拿到 publicKey 后，/v3 数据接口的报文密钥改用
        ``derive_key(tms, publicKey)``；失败时静默回退到 appSecret。
        """
        if not self.token:
            return
        url = f"https://{OPEN_API_BOSS}/account/userToken/login"
        body = {"request": {"appKey": self.app_key, "authorization": self.token}}
        try:
            session = await self._ensure_session()
            async with session.post(
                url,
                json=body,
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "uuid": self.uuid,
                    "appKey": self.app_key,
                },
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status >= 400:
                    return
                payload = await resp.json(content_type=None)
        except Exception:  # pragma: no cover - 网络/解析失败都不影响登录
            return
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return
        public_key = data.get("publicKey")
        if isinstance(public_key, str) and public_key:
            # 只做记录：实测报文密钥仍由 appSecret 派生（derive_key(tms, app_secret)）
            self.public_key = public_key
        new_token = data.get("accessToken")
        if isinstance(new_token, str) and new_token:
            self.token = new_token

    async def list_devices(self, page: int = 1, size: int = 50) -> list[dict[str, Any]]:
        # 实测：必须 POST + body {page, limit}（GET/query 会 404）
        payload = await self.raw_request(
            ENDPOINTS["device_list"][1],
            method="POST",
            body={"page": page, "limit": size},
        )
        data = payload.get("data")
        if isinstance(data, Mapping):
            rows = data.get("data") or data.get("list") or data.get("rows") or []
        else:
            rows = data if isinstance(data, list) else []
        return [r for r in rows if isinstance(r, Mapping)]

    async def get_device_token(self, sns: Iterable[str]) -> dict[str, str]:
        # 实测：逐台调用，body 需要 {deviceNo, isLatest}
        out: dict[str, str] = {}
        for sn in sns:
            try:
                payload = await self.raw_request(
                    ENDPOINTS["device_token"][1], body={"deviceNo": sn, "isLatest": 1}
                )
            except Exception:  # noqa: BLE001
                continue
            data = payload.get("data") or {}
            if isinstance(data, Mapping):
                token = data.get("token") or data.get("adminToken")
                if token:
                    out[sn] = token
        return out

    async def get_prop_status(self, sns: Iterable[str]) -> list[dict[str, Any]]:
        payload = await self.raw_request(
            ENDPOINTS["prop_status"][1], body={"sns": list(sns)}
        )
        return _as_list(payload)

    async def send_command(
        self,
        sn: str,
        name: str,
        params: Mapping[str, Any] | None = None,
        *,
        channel: int = 0,
    ) -> dict[str, Any]:
        """``/v3/access/send/command`` — send one device command (``OP...`` id)."""
        body = {"sn": sn, "Name": name, "Channel": channel}
        if params:
            body.update(params)
        return await self.raw_request(ENDPOINTS["send_command"][1], body=body)

    async def get_notify_logs(
        self, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """``/v3/message/getNotifyLogList`` — the push/notify message log.

        This is the same feed the vendor app turns into a banner, so polling it
        is the closest thing to "instant" without the vendor push channel.
        """
        payload = await self.raw_request(
            ENDPOINTS["notify_log"][1],
            body={"pageNum": page, "pageSize": size},
        )
        return _as_list(payload)

    async def get_open_records(
        self,
        sn: str,
        start_ms: int,
        end_ms: int,
        *,
        page: int = 1,
        size: int = 50,
    ) -> list[dict[str, Any]]:
        payload = await self.raw_request(
            ENDPOINTS["record_report"][1],
            body={
                "sn": sn,
                "startTime": start_ms,
                "endTime": end_ms,
                "pageNum": page,
                "pageSize": size,
            },
        )
        return _as_list(payload)


def _as_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        for key in ("list", "records", "items", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return [dict(data)]
    page = payload.get("page")
    if isinstance(page, Mapping) and isinstance(page.get("records"), list):
        return page["records"]
    return []
