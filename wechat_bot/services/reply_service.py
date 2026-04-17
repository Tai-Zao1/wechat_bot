"""自动回复的回复源调度服务。

把 API 模式、本地百炼模式、规则兜底模式统一收口，避免脚本层同时处理：

- 客户端初始化
- 请求参数日志
- 接口异常与鉴权失效
- “事件无法调用任何订户”后的在线回调
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from client_api import (
    WeChatAIAuthenticationError,
    WeChatAIClient,
)
from wechat_bot.core.network import get_check_online_base_url
from wechat_bot.services.bailian_client import (
    DEFAULT_BAILIAN_ENDPOINT,
    DEFAULT_BAILIAN_SYSTEM_PROMPT,
    LocalBailianClient,
    LocalBailianConfig,
    mask_secret,
)


AUTH_EXPIRED_MARKER = "[AUTH_EXPIRED]"
AUTO_REPLY_STOP_MARKER = "[AUTO_REPLY_STOP]"
NO_SUBSCRIBER_ERROR_MARKERS = ("事件无法调用任何订户", "无法调用任何订户")


@dataclass(slots=True)
class ReplyRequest:
    """一次自动回复请求的业务参数。"""

    wechat_id: str
    message: str
    nickname: str = ""
    display_name: str = ""
    to_nickname: str = ""


@dataclass(slots=True)
class ReplyServiceConfig:
    """自动回复服务的运行配置。"""

    mode: str = "api"
    bailian_app_id: str = ""
    bailian_api_key: str = ""
    bailian_system_prompt: str = DEFAULT_BAILIAN_SYSTEM_PROMPT
    bailian_endpoint: str = DEFAULT_BAILIAN_ENDPOINT
    bailian_timeout: float = 20.0
    check_online_base_url: str = field(default_factory=get_check_online_base_url)


class ReplyServiceStop(RuntimeError):
    """需要停止自动回复时抛出的受控异常。"""

    def __init__(self, *, exit_code: int, marker: str, message: str) -> None:
        super().__init__(message)
        self.exit_code = int(exit_code)
        self.marker = str(marker)
        self.message = str(message)

    def emit(self, printer: Callable[[str], None]) -> None:
        printer(f"{self.marker} {self.message}")


class AutoReplyService:
    """封装自动回复脚本的回复源选择与异常处理。"""

    def __init__(
        self,
        config: ReplyServiceConfig,
        *,
        logger: Callable[[str, object], None] | None = None,
    ) -> None:
        self.config = config
        self._logger = logger
        self._api_client: WeChatAIClient | None = None
        self._local_client: LocalBailianClient | None = None

    @property
    def mode(self) -> str:
        mode = str(self.config.mode or "api").strip().lower()
        if mode not in {"api", "local", "rules"}:
            return "api"
        return mode

    @property
    def api_client(self) -> WeChatAIClient | None:
        return self._api_client

    def prime_clients(self) -> None:
        """按当前模式预热客户端，提前输出可读日志。"""
        if self.mode == "api":
            self._api_client = self._load_chat_api_client()
        elif self.mode == "local":
            self._local_client = self._load_local_bailian_client()

    def make_reply(self, request: ReplyRequest, fallback_reply: Callable[[], str]) -> str:
        """根据当前模式获取回复，失败时回退到本地规则。"""
        if self.mode == "local":
            return self._make_local_reply(request, fallback_reply)
        if self.mode == "rules":
            return fallback_reply()
        return self._make_api_reply(request, fallback_reply)

    def handle_no_subscriber_error(
        self,
        exc: Exception,
        *,
        wechat_id: str,
        context: str,
        stop_code: int,
    ) -> bool:
        """处理接口层的“事件无法调用任何订户”错误。"""
        if not self.has_no_subscriber_error(exc):
            return False
        ok, detail = self.call_check_online(wechat_id, client=self._api_client)
        if ok:
            self._log("在线检查回调", f"{context or '-'} -> checkOnline 成功: {detail}")
        else:
            self._log("在线检查回调失败", f"{context or '-'} -> {detail}")
        raise ReplyServiceStop(
            exit_code=stop_code,
            marker=AUTO_REPLY_STOP_MARKER,
            message=(
                f"{context or '自动回复'}检测到“事件无法调用任何订户”，"
                f"已调用 checkOnline，停止自动回复。wechatId={wechat_id or 'wxid_unknown'}"
            ),
        )

    @staticmethod
    def has_no_subscriber_error(exc: Exception) -> bool:
        texts: list[str] = [str(exc or "")]
        response_data = getattr(exc, "response_data", None)
        if response_data is not None:
            try:
                texts.append(json.dumps(response_data, ensure_ascii=False))
            except Exception:
                texts.append(str(response_data))
        merged = "\n".join(texts)
        return any(marker in merged for marker in NO_SUBSCRIBER_ERROR_MARKERS)

    def call_check_online(
        self,
        wechat_id: str,
        *,
        timeout_s: float = 8.0,
        client: WeChatAIClient | None = None,
    ) -> tuple[bool, str]:
        wxid = str(wechat_id or "").strip()
        if not wxid:
            return False, "缺少当前登录微信号"
        target_client = client
        if target_client is None:
            try:
                target_client = WeChatAIClient.from_saved_state(auto_persist=False)
            except Exception as exc:
                return False, f"创建客户端失败: {exc}"
        try:
            result = target_client.check_online(
                wxid=wxid,
                base_url=self.config.check_online_base_url,
                timeout=max(timeout_s, 1.0),
                require_auth=True,
            )
            body = str(result.get("body", "")).replace("\n", " ")
            if len(body) > 240:
                body = body[:240] + "..."
            return True, f"path={result.get('path')} status={result.get('status')} body={body}"
        except Exception as exc:
            return False, str(exc)

    def _make_local_reply(self, request: ReplyRequest, fallback_reply: Callable[[], str]) -> str:
        if self._local_client is None:
            self._local_client = self._load_local_bailian_client()
        if self._local_client is not None:
            try:
                self._log(
                    "本地百炼参数",
                    {
                        "appId": self.config.bailian_app_id,
                        "content": request.message,
                        "nickname": request.nickname,
                        "displayName": request.display_name,
                        "toNickname": request.to_nickname,
                    },
                )
                result = self._local_client.chat(
                    message=request.message,
                    nickname=request.nickname,
                    display_name=request.display_name,
                    to_nickname=request.to_nickname,
                )
                reply = str(result.get("reply") or "").strip()
                if reply:
                    source = result.get("reply_source") or "local_bailian"
                    self._log("本地百炼回复", f"{request.display_name or request.to_nickname} source={source}")
                    return reply
                self._log("本地百炼回复为空", f"{request.display_name or request.to_nickname} -> 使用本地规则回复")
            except KeyboardInterrupt as exc:
                self._log("本地百炼调用中断", f"{request.display_name or request.to_nickname} -> {exc}")
            except Exception as exc:
                self._log("本地百炼调用失败", f"{request.display_name or request.to_nickname} -> {exc}")
                self._local_client = None
        return fallback_reply()

    def _make_api_reply(self, request: ReplyRequest, fallback_reply: Callable[[], str]) -> str:
        if self._api_client is None:
            self._api_client = self._load_chat_api_client()
        if self._api_client is not None:
            try:
                peer_name = request.display_name or request.to_nickname or "-"
                self._log(
                    "聊天接口请求",
                    f"{peer_name} -> /autoWx/chat wechatId={request.wechat_id or 'wxid_unknown'} "
                    f"nickname={request.nickname or '-'} toNickname={request.to_nickname or '-'}",
                )
                self._log(
                    "聊天接口参数",
                    {
                        "wechatId": request.wechat_id,
                        "content": request.message,
                        "nickname": request.nickname,
                        "displayName": request.display_name,
                        "toNickname": request.to_nickname,
                    },
                )
                result = self._api_client.chat(
                    wxid=request.wechat_id,
                    message=request.message,
                    nickname=request.nickname,
                    display_name=request.display_name,
                    to_nickname=request.to_nickname,
                )
                reply = str(result.get("reply") or "").strip()
                if reply:
                    reply_source = str(result.get("reply_source") or "").strip() or "-"
                    record_id = result.get("record_id")
                    self._log("聊天接口回复", f"{peer_name} source={reply_source} record_id={record_id}")
                    return reply
                self._log("聊天接口回复为空", f"{peer_name} -> 使用本地规则回复")
            except WeChatAIAuthenticationError as exc:
                self._log("聊天接口鉴权失效", f"{request.display_name or request.to_nickname} -> {exc}")
                raise ReplyServiceStop(
                    exit_code=401,
                    marker=AUTH_EXPIRED_MARKER,
                    message=f"自动回复鉴权失效: {exc}",
                ) from exc
            except KeyboardInterrupt as exc:
                self._log("聊天接口调用中断", f"{request.display_name or request.to_nickname} -> {exc}")
            except Exception as exc:
                self.handle_no_subscriber_error(
                    exc,
                    wechat_id=request.wechat_id,
                    context=str(request.display_name or request.to_nickname or "-"),
                    stop_code=460,
                )
                self._log("聊天接口调用失败", f"{request.display_name or request.to_nickname} -> {exc}")
                self._api_client = None
        return fallback_reply()

    def _load_chat_api_client(self) -> WeChatAIClient | None:
        try:
            client = WeChatAIClient.from_saved_state()
        except Exception as exc:
            self._log("聊天接口", f"读取登录态失败，使用本地规则回复: {exc}")
            return None
        if not client.is_authenticated:
            self._log("聊天接口", "当前未登录 API，使用本地规则回复")
            return None
        self._log("聊天接口", "已启用 /autoWx/chat")
        return client

    def _load_local_bailian_client(self) -> LocalBailianClient | None:
        config = LocalBailianConfig(
            app_id=str(self.config.bailian_app_id or "").strip(),
            api_key=str(self.config.bailian_api_key or "").strip(),
            system_prompt=str(self.config.bailian_system_prompt or "").strip(),
            endpoint=str(self.config.bailian_endpoint or DEFAULT_BAILIAN_ENDPOINT).strip(),
            timeout=float(self.config.bailian_timeout or 20.0),
        )
        if not config.enabled:
            self._log("本地百炼", "缺少 appId/apiKey，使用本地规则回复")
            return None
        try:
            client = LocalBailianClient(config)
        except Exception as exc:
            self._log("本地百炼", f"初始化失败，使用本地规则回复: {exc}")
            return None
        self._log("本地百炼", f"已启用 appId={config.app_id} apiKey={mask_secret(config.api_key)}")
        return client

    def _log(self, key: str, value: object) -> None:
        if self._logger is not None:
            self._logger(key, value)


__all__ = [
    "AUTH_EXPIRED_MARKER",
    "AUTO_REPLY_STOP_MARKER",
    "AutoReplyService",
    "ReplyRequest",
    "ReplyServiceConfig",
    "ReplyServiceStop",
]
