#!/usr/bin/env python3
"""WeChatAI 客户端 API 封装。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import winreg
except Exception:  # pragma: no cover - non-Windows env
    winreg = None


DEFAULT_TIMEOUT = 20.0
MAX_DEVICE_ID_LEN = 64
DEFAULT_FRIEND_SYNC_PATH = "/client/friends/sync"
DEFAULT_CHAT_PATH = "/autoWx/chat"
DEFAULT_CHECK_ONLINE_PATHS = ("/autoWx/checkOnline",)
DEFAULT_NEED_ADD_PHONE_LIST_PATH = "/autoWx/getNeedAddPhoneList"
DEFAULT_WX_CHECK_PATH = "/autoWx/wxCheck"


def _load_dotenv_if_present() -> None:
    """Load .env values without overriding process-level environment."""
    candidate_files = [
        Path(__file__).resolve().parents[1] / ".env",
        Path.cwd() / ".env",
    ]
    loaded: set[Path] = set()
    for env_path in candidate_files:
        if env_path in loaded or not env_path.is_file():
            continue
        loaded.add(env_path)
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


def _resolve_base_urls() -> tuple[str, str]:
    env = str(os.getenv("PYWECHAT_ENV", "prod")).strip().lower()
    local_default = "http://127.0.0.1:666/yct-crm-admin"
    env_suffix = "LOCAL" if env in {"local", "dev", "development"} else "PROD"
    api_base = (
        os.getenv("PYWECHAT_API_BASE_URL")
        or os.getenv(f"PYWECHAT_API_BASE_URL_{env_suffix}")
        or local_default
    )
    check_base = (
        os.getenv("PYWECHAT_CHECK_ONLINE_BASE_URL")
        or os.getenv(f"PYWECHAT_CHECK_ONLINE_BASE_URL_{env_suffix}")
        or api_base
    )
    return str(api_base).strip(), str(check_base).strip()


_load_dotenv_if_present()
_resolved_api_base, _resolved_check_online_base = _resolve_base_urls()
DEFAULT_BASE_URL = _resolved_api_base
DEFAULT_CHECK_ONLINE_BASE_URL = _resolved_check_online_base


def normalize_device_id(device_id: str | None) -> str:
    text = str(device_id or "").strip()
    if not text:
        return ""
    if len(text) <= MAX_DEVICE_ID_LEN:
        return text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_client_logger() -> logging.Logger:
    logger = logging.getLogger("PyWeChatBot.client_api")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def _read_windows_machine_guid() -> str:
    if platform.system().lower() != "windows" or winreg is None:
        return ""
    key_paths = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Cryptography"),
    )
    for root, key_path in key_paths:
        try:
            with winreg.OpenKey(root, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                guid = str(value or "").strip()
                if guid:
                    return guid
        except Exception:
            continue
    return ""


def _run_wmic_value(alias: str, field: str) -> str:
    if platform.system().lower() != "windows":
        return ""
    candidates = (
        ["wmic", alias, "get", field, "/value"],
        ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance -ClassName Win32_{alias}).{field}"],
    )
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6, check=False)
        except Exception:
            continue
        raw = (proc.stdout or "").strip()
        if not raw:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                _, value = line.split("=", 1)
                value = value.strip()
            else:
                value = line.strip()
            if value and value not in {"To be filled by O.E.M.", "Default string", "System Serial Number"}:
                return value
    return ""


def resolve_device_fingerprint_id() -> str:
    parts = [
        _read_windows_machine_guid(),
        _run_wmic_value("BaseBoard", "SerialNumber"),
        _run_wmic_value("BIOS", "SerialNumber"),
    ]
    raw = "|".join(part.strip() for part in parts if str(part or "").strip())
    if raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return normalize_device_id(f"winfp-{digest}")
    return ""


def get_client_app_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home() / "AppData" / "Local")
    root = Path(base) / "PyWeChatBot"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_client_state_path() -> Path:
    config_dir = get_client_app_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "wechat_ai_client.json"


class WeChatAIClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        business_code: int | None = None,
        response_data: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.business_code = business_code
        self.response_data = response_data


class WeChatAINetworkError(WeChatAIClientError):
    pass


class WeChatAIAuthenticationError(WeChatAIClientError):
    pass


class WeChatAIForbiddenError(WeChatAIClientError):
    pass


class WeChatAIValidationError(WeChatAIClientError):
    pass


class WeChatAIRateLimitError(WeChatAIClientError):
    pass


class WeChatAIServerError(WeChatAIClientError):
    pass


@dataclass(slots=True)
class WeChatAIClientState:
    base_url: str
    device_id: str
    token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "device_id": self.device_id,
            "token": self.token,
        }


class WeChatAIClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        device_id: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        state_path: str | os.PathLike[str] | None = None,
        auto_persist: bool = True,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.state_path = Path(state_path) if state_path else get_client_state_path()
        self.auto_persist = bool(auto_persist)
        self.logger = get_client_logger()
        self.device_id = normalize_device_id(device_id) or self._load_or_create_device_id()
        self.token = str(token).strip() if token else None
        if self.auto_persist:
            self.save_state()

    def _mask_token(self, token: str | None) -> str:
        text = str(token or "").strip()
        if not text:
            return ""
        if len(text) <= 12:
            return "***"
        return f"{text[:6]}...{text[-4:]}"

    def _mask_device_id(self, device_id: str | None) -> str:
        text = str(device_id or "").strip()
        if not text:
            return ""
        if len(text) <= 12:
            return text
        return f"{text[:8]}...{text[-6:]}"

    def _sanitize_value_for_log(self, value: Any, key: str = "") -> Any:
        key_l = str(key or "").lower()
        if isinstance(value, dict):
            return {str(k): self._sanitize_value_for_log(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value_for_log(item, key) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_value_for_log(item, key) for item in value)
        if value is None:
            return None
        if any(marker in key_l for marker in ("password", "passwd", "secret")):
            return "***"
        if "device" in key_l:
            return self._mask_device_id(str(value))
        if "token" in key_l or key_l in {"authorization", "auth"}:
            return self._mask_token(str(value))
        return value

    def _sanitize_headers_for_log(self, headers: dict[str, str]) -> dict[str, str]:
        return self._sanitize_value_for_log(dict(headers))

    def _sanitize_body_for_log(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        return self._sanitize_value_for_log(dict(payload))

    def _sanitize_response_for_log(self, raw: str, limit: int = 1000) -> str:
        text = str(raw or "")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text[:limit]
        safe = self._sanitize_value_for_log(parsed)
        return json.dumps(safe, ensure_ascii=False)[:limit]

    @classmethod
    def from_saved_state(
        cls,
        *,
        state_path: str | os.PathLike[str] | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        auto_persist: bool = True,
    ) -> "WeChatAIClient":
        path = Path(state_path) if state_path else get_client_state_path()
        state = cls.load_state(path)
        return cls(
            base_url=base_url or DEFAULT_BASE_URL,
            device_id=state.device_id,
            token=state.token,
            timeout=timeout,
            state_path=path,
            auto_persist=auto_persist,
        )

    @staticmethod
    def load_state(path: str | os.PathLike[str] | None = None) -> WeChatAIClientState:
        target = Path(path) if path else get_client_state_path()
        if target.is_file():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}
        base_url = str(DEFAULT_BASE_URL).rstrip("/")
        persisted_device_id = normalize_device_id(data.get("device_id"))
        device_id = persisted_device_id or resolve_device_fingerprint_id() or uuid.uuid4().hex
        token = str(data.get("token")).strip() if data.get("token") else None
        return WeChatAIClientState(base_url=base_url, device_id=device_id, token=token)

    def save_state(self) -> None:
        state = WeChatAIClientState(
            base_url=self.base_url,
            device_id=self.device_id,
            token=self.token,
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear_state(self, *, keep_device_id: bool = True) -> None:
        self.token = None
        if not keep_device_id:
            self.device_id = resolve_device_fingerprint_id() or uuid.uuid4().hex
        if self.auto_persist:
            self.save_state()

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)

    def login(self, username: str, password: str, *, device_id: str | None = None) -> dict[str, Any]:
        payload = {
            "username": str(username).strip(),
            "password": str(password)
        }
        result = self._request("POST", "/web/pcLogin", json_body=payload, require_auth=False)
        data = result.get("data") or {}
        token = (
            data.get("api_token")
            or data.get("token")
            or data.get("access_token")
            or result.get("api_token")
            or result.get("token")
            or result.get("access_token")
        )
        if not token:
            raise WeChatAIServerError("登录响应缺少 token", response_data=result)
        self.token = str(token).strip()
        if self.auto_persist:
            self.save_state()
        return result

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/client/me")

    def chat(
        self,
        *,
        wxid: str,
        message: str,
        nickname: str | None = None,
        display_name: str | None = None,
        to_nickname: str | None = None,
        chat_path: str = DEFAULT_CHAT_PATH,
    ) -> dict[str, Any]:
        payload = {
            "wechatId": str(wxid or "").strip(),
            "content": str(message or "").strip(),
            "nickname": str(nickname or "").strip(),
            "displayName": str(display_name or "").strip(),
            "toNickname": str(to_nickname or "").strip(),
        }
        if not payload["wechatId"]:
            raise WeChatAIValidationError("chat 请求缺少 wechatId")
        if not payload["content"]:
            raise WeChatAIValidationError("chat 请求缺少 message")
        result = self._request("POST", chat_path, json_body=payload)
        data = result.get("data")
        if isinstance(data, dict):
            reply = str(
                data.get("reply")
                or data.get("content")
                or ""
            ).strip()
            normalized = dict(data)
        elif data is not None:
            reply = str(data).strip()
            normalized = {"data": data}
        else:
            reply = str(
                result.get("reply")
                or result.get("content")
                or ""
            ).strip()
            normalized = {}
        if not reply:
            raise WeChatAIServerError("聊天接口未返回 reply", response_data=result)
        normalized["reply"] = reply
        return normalized

    def check_online(
        self,
        *,
        wxid: str,
        base_url: str = DEFAULT_CHECK_ONLINE_BASE_URL,
        candidate_paths: tuple[str, ...] = DEFAULT_CHECK_ONLINE_PATHS,
        timeout: float | None = None,
        require_auth: bool = True,
    ) -> dict[str, Any]:
        wechat_id = str(wxid or "").strip()
        if not wechat_id:
            raise WeChatAIValidationError("checkOnline 请求缺少 wechatId")
        wechat_id_path = urllib.parse.quote(wechat_id, safe="")
        clean_base = str(base_url or DEFAULT_CHECK_ONLINE_BASE_URL).rstrip("/")
        timeout_s = float(timeout if timeout is not None else self.timeout)
        headers: dict[str, str] = {}
        if require_auth:
            headers.update(self.authenticated_headers())
        elif self.device_id:
            headers["X-Device-Id"] = self.device_id
        last_error: Exception | None = None
        last_error_path = ""
        for path in candidate_paths:
            clean_path = "/" + str(path or "").lstrip("/")
            used_path = f"{clean_path}/{wechat_id_path}"
            url = f"{clean_base}{used_path}"
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                started = time.perf_counter()
                with urllib.request.urlopen(request, timeout=timeout_s) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    status_code = int(getattr(response, "status", 200))
                self.logger.info(
                    "api response GET %s status=%s %.2fms headers=%s body=%s",
                    used_path,
                    status_code,
                    (time.perf_counter() - started) * 1000,
                    self._sanitize_headers_for_log(headers),
                    self._sanitize_response_for_log(raw),
                )
                try:
                    body: Any = json.loads(raw)
                except json.JSONDecodeError:
                    body = raw
                if isinstance(body, dict):
                    business_code = body.get("code")
                    if isinstance(business_code, bool):
                        business_code = None
                    if isinstance(business_code, str) and business_code.strip().isdigit():
                        business_code = int(business_code.strip())
                    if business_code is not None and int(business_code) != 200:
                        message = str(body.get("msg") or body.get("message") or "checkOnline 业务失败").strip()
                        raise WeChatAIServerError(
                            message,
                            status_code=status_code,
                            business_code=int(business_code),
                            response_data=body,
                        )
                return {"path": used_path, "status": status_code, "body": body}
            except Exception as exc:
                last_error = exc
                last_error_path = used_path
                self.logger.warning(
                    "api network error GET %s headers=%s error=%s",
                    used_path,
                    self._sanitize_headers_for_log(headers),
                    exc,
                )
        raise WeChatAINetworkError(f"checkOnline 调用失败: path={last_error_path} error={last_error}")

    def sync_friend_profiles(
        self,
        *,
        wxid: str,
        friends: list[dict[str, Any]],
        sync_path: str = DEFAULT_FRIEND_SYNC_PATH,
    ) -> dict[str, Any]:
        payload = {
            "wxid": str(wxid or "").strip(),
            "friends": friends,
        }
        return self._request("POST", sync_path, json_body=payload)

    def get_need_add_phone_list(
        self,
        *,
        wechat_id: str,
        list_path: str = DEFAULT_NEED_ADD_PHONE_LIST_PATH,
    ) -> dict[str, Any]:
        wxid = str(wechat_id or "").strip()
        if not wxid:
            raise WeChatAIValidationError("获取待加手机号列表缺少 wechatId")
        safe_wxid = urllib.parse.quote(wxid, safe="")
        result = self._request("GET", f"{list_path.rstrip('/')}/{safe_wxid}")
        data = result.get("data")
        if data is None:
            return {"need_list": [], "system_prompt": ""}
        if isinstance(data, list):
            raw_need_list = data
            system_prompt = ""
        elif isinstance(data, dict):
            raw_need_list = data.get("needList") or []
            system_prompt = str(data.get("systemPrompt") or "").strip()
        else:
            raise WeChatAIServerError("待加手机号接口返回格式不合法", response_data=result)
        phones: list[str] = []
        if not isinstance(raw_need_list, list):
            raise WeChatAIServerError("待加手机号接口 needList 格式不合法", response_data=result)
        for item in raw_need_list:
            text = str(item or "").strip()
            if text:
                phones.append(text)
        return {
            "need_list": phones,
            "system_prompt": system_prompt,
        }

    def wx_check(
        self,
        *,
        wechat_id: str,
        mobile: str,
        check_path: str = DEFAULT_WX_CHECK_PATH,
    ) -> dict[str, Any]:
        wxid = str(wechat_id or "").strip()
        phone = str(mobile or "").strip()
        if not wxid:
            raise WeChatAIValidationError("wxCheck 请求缺少 wechatId")
        if not phone:
            raise WeChatAIValidationError("wxCheck 请求缺少 mobile")
        payload = {
            "wechatId": wxid,
            "mobile": phone,
        }
        return self._request("POST", check_path, json_body=payload)

    def authenticated_headers(self) -> dict[str, str]:
        if not self.token:
            raise WeChatAIAuthenticationError("未登录，缺少 staff token")
        return {
            "Authorization": self.token,
            "X-Staff-Token": self.token,
            "X-Device-Id": self.device_id,
        }

    def _load_or_create_device_id(self) -> str:
        state = self.load_state(self.state_path)
        return state.device_id

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        require_auth: bool = True,
    ) -> dict[str, Any]:
        url = self._make_url(path)
        headers = {"Content-Type": "application/json"}
        if require_auth:
            headers.update(self.authenticated_headers())
        elif self.device_id:
            headers["X-Device-Id"] = self.device_id

        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method.upper(),
        )
        started = time.perf_counter()
        self.logger.info(
            "api request %s %s url=%s headers=%s body=%s",
            method.upper(),
            path,
            url,
            self._sanitize_headers_for_log(headers),
            self._sanitize_body_for_log(json_body),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                status_code = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            self.logger.warning(
                "api http error %s %s %.2fms response=%s",
                method.upper(),
                path,
                (time.perf_counter() - started) * 1000,
                self._sanitize_response_for_log(raw),
            )
            return self._raise_from_response(raw, status_code=int(exc.code))
        except urllib.error.URLError as exc:
            self.logger.warning(
                "api network error %s %s %.2fms error=%s",
                method.upper(),
                path,
                (time.perf_counter() - started) * 1000,
                exc,
            )
            raise WeChatAINetworkError(f"网络异常: {exc}") from exc
        self.logger.info(
            "api response %s %s status=%s %.2fms body=%s",
            method.upper(),
            path,
            status_code,
            (time.perf_counter() - started) * 1000,
            self._sanitize_response_for_log(raw),
        )
        return self._parse_success_response(raw, status_code=status_code)

    def _make_url(self, path: str) -> str:
        clean_path = "/" + str(path).lstrip("/")
        return urllib.parse.urljoin(f"{self.base_url}/", clean_path.lstrip("/"))

    def _extract_message(self, result: dict[str, Any], status_code: int | None = None) -> str:
        candidates = (
            result.get("message"),
            result.get("msg"),
            result.get("error"),
            result.get("errmsg"),
            result.get("detail"),
        )
        for item in candidates:
            text = str(item or "").strip()
            if text:
                return text
        if status_code is not None:
            return f"请求失败: HTTP {status_code}"
        return "请求失败"

    def _extract_business_code(self, result: dict[str, Any]) -> int | None:
        for key in ("code", "status", "statusCode"):
            value = result.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
        success = result.get("success")
        if isinstance(success, bool):
            return 200 if success else 500
        return None

    def _parse_success_response(self, raw: str, *, status_code: int) -> dict[str, Any]:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WeChatAIServerError(
                "服务端返回了非 JSON 数据",
                status_code=status_code,
                response_data=raw,
            ) from exc
        if not isinstance(result, dict):
            raise WeChatAIServerError(
                "服务端响应格式不合法",
                status_code=status_code,
                response_data=result,
            )
        business_code = self._extract_business_code(result)
        if status_code >= 400 or (business_code is not None and business_code != 200):
            self._raise_by_status(
                message=self._extract_message(result, status_code=status_code),
                status_code=status_code,
                business_code=business_code,
                response_data=result,
            )
        return result

    def _raise_from_response(self, raw: str, *, status_code: int) -> dict[str, Any]:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            self._raise_by_status(
                message=f"HTTP {status_code}: {raw or '请求失败'}",
                status_code=status_code,
                response_data=raw,
            )
        if not isinstance(result, dict):
            self._raise_by_status(
                message=f"HTTP {status_code}: 响应格式不合法",
                status_code=status_code,
                response_data=result,
            )
        self._raise_by_status(
            message=self._extract_message(result, status_code=status_code),
            status_code=status_code,
            business_code=self._extract_business_code(result),
            response_data=result,
        )

    def _raise_by_status(
        self,
        *,
        message: str,
        status_code: int | None = None,
        business_code: int | None = None,
        response_data: Any = None,
    ) -> None:
        error_cls: type[WeChatAIClientError]
        if status_code == 401 or business_code == 401:
            self.token = None
            if self.auto_persist:
                self.save_state()
            error_cls = WeChatAIAuthenticationError
        elif status_code == 403 or business_code == 403:
            error_cls = WeChatAIForbiddenError
        elif status_code in {400, 404, 422}:
            error_cls = WeChatAIValidationError
        elif status_code == 429:
            error_cls = WeChatAIRateLimitError
        elif status_code is not None and status_code >= 500:
            error_cls = WeChatAIServerError
        else:
            error_cls = WeChatAIClientError
        raise error_cls(
            message,
            status_code=status_code,
            business_code=business_code,
            response_data=response_data,
        )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CHECK_ONLINE_BASE_URL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_FRIEND_SYNC_PATH",
    "DEFAULT_NEED_ADD_PHONE_LIST_PATH",
    "DEFAULT_WX_CHECK_PATH",
    "WeChatAIAuthenticationError",
    "WeChatAIClient",
    "WeChatAIClientError",
    "WeChatAIClientState",
    "WeChatAIForbiddenError",
    "WeChatAINetworkError",
    "WeChatAIRateLimitError",
    "WeChatAIServerError",
    "WeChatAIValidationError",
    "get_client_app_root",
    "get_client_logger",
    "get_client_state_path",
    "resolve_device_fingerprint_id",
]
