#!/usr/bin/env python3
"""本地阿里百炼应用调用封装。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BAILIAN_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion"
DEFAULT_BAILIAN_SYSTEM_PROMPT = "你是微信自动回复助手。请用简短、礼貌、自然的中文直接回复用户消息，不要解释你是模型。"


@dataclass(slots=True)
class LocalBailianConfig:
    app_id: str
    api_key: str
    system_prompt: str = DEFAULT_BAILIAN_SYSTEM_PROMPT
    endpoint: str = DEFAULT_BAILIAN_ENDPOINT
    timeout: float = 20.0

    @property
    def enabled(self) -> bool:
        return bool(self.app_id.strip() and self.api_key.strip())


class LocalBailianError(RuntimeError):
    pass


def mask_secret(text: str | None) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if len(value) <= 10:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


class LocalBailianClient:
    def __init__(self, config: LocalBailianConfig) -> None:
        self.config = config
        if not self.config.enabled:
            raise LocalBailianError("本地百炼配置缺少 appId 或 apiKey")

    def chat(
        self,
        *,
        message: str,
        nickname: str = "",
        display_name: str = "",
        to_nickname: str = "",
    ) -> dict[str, Any]:
        content = str(message or "").strip()
        if not content:
            raise LocalBailianError("本地百炼请求缺少 message")
        prompt = self._build_prompt(
            message=content,
            nickname=nickname,
            display_name=display_name,
            to_nickname=to_nickname,
        )
        body = {
            "input": {
                "prompt": prompt,
            },
            "parameters": {},
        }
        url = self.config.endpoint.format(app_id=urllib.parse.quote(self.config.app_id.strip(), safe=""))
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=float(self.config.timeout)) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise LocalBailianError(f"百炼 HTTP {exc.code}: {raw[:300]}") from exc
        except urllib.error.URLError as exc:
            raise LocalBailianError(f"百炼网络异常: {exc}") from exc
        if status >= 400:
            raise LocalBailianError(f"百炼 HTTP {status}: {raw[:300]}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LocalBailianError(f"百炼返回非 JSON: {raw[:300]}") from exc
        reply = self._extract_reply(payload)
        if not reply:
            raise LocalBailianError(f"百炼响应缺少回复内容: {json.dumps(payload, ensure_ascii=False)[:300]}")
        return {
            "reply": reply,
            "reply_source": "local_bailian",
            "raw": payload,
        }

    def _build_prompt(self, *, message: str, nickname: str, display_name: str, to_nickname: str) -> str:
        lines = []
        system_prompt = str(self.config.system_prompt or "").strip()
        if system_prompt:
            lines.append(system_prompt)
        if nickname:
            lines.append(f"当前账号昵称：{nickname}")
        peer_name = to_nickname or display_name
        if peer_name:
            lines.append(f"对方昵称/备注：{peer_name}")
        lines.append(f"用户消息：{message}")
        return "\n".join(lines)

    @staticmethod
    def _extract_reply(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        output = payload.get("output")
        if isinstance(output, dict):
            for key in ("text", "answer", "content"):
                value = str(output.get(key) or "").strip()
                if value:
                    return value
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("reply", "text", "answer", "content"):
                value = str(data.get(key) or "").strip()
                if value:
                    return value
        for key in ("reply", "text", "answer", "content"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return ""


__all__ = [
    "DEFAULT_BAILIAN_ENDPOINT",
    "DEFAULT_BAILIAN_SYSTEM_PROMPT",
    "LocalBailianClient",
    "LocalBailianConfig",
    "LocalBailianError",
    "mask_secret",
]
