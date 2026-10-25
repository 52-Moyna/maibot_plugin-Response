"""回复确认标记（Reply Confirm）—— MaiBot 插件。

机器人回复某条消息时，被回复的那条消息上会自动多出一个表情回应（默认 ✅），
群里一眼就知道它在回谁，不用挂着灰引用框。

实现走宿主两个发送钩子，不改主程序、不拦截消息内容：

- ``send_service.before_send``：在插入引用组件**之前**触发。若开启「用表情替代引用框」，
  这里把 ``set_reply`` 改成 ``False``，引用框就不插入了；``reply_message_id`` 不碰，
  宿主会照原样继续往下传（``send_service.py`` 里该字段独立于 ``set_reply`` 读取）。
- ``send_service.after_send``：平台确认发送成功后触发，带 ``sent`` 与 ``reply_message_id``。
  这里调 OneBot 的 ``set_msg_emoji_like`` 给被回复的消息贴表情。

因此「贴表情」与「引用框」是解耦的：有引用目标就有表情标记，是否显示灰引用框单独配置。

宿主契约（MaiBot 1.0.0+ / maibot-plugin-sdk 2.x）：

- 两个 Hook 都允许修改 kwargs，``before_send`` 以 ``HookMode.BLOCKING`` 注册并在
  ``modified_kwargs`` 回传改写后的参数；
- ``after_send`` 只观察，不回传修改；
- 任一环节失败都只记日志、放行，绝不影响消息发送本身。
"""

from __future__ import annotations

from typing import Any
import asyncio
import http.client
import json
import logging

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

__all__ = [
    "PluginSection",
    "ReplyConfirmConfig",
    "ReplyConfirmPlugin",
    "create_plugin",
]

#: 插件 ID，用于兜底 Logger 名称。
PLUGIN_ID = "workbuddy.reply_confirm"

#: ✅（U+2705）的 QQ 表情回应 ID，实测可贴。
DEFAULT_EMOJI_ID = "2705"

#: SnowLuma / NapCat 的 OneBot v11 HTTP 默认端口。
DEFAULT_PORT = 3000

_FALLBACK_LOGGER = logging.getLogger(PLUGIN_ID)


class PluginSection(PluginConfigBase):
    """插件主体配置。"""

    __ui_label__ = "回复确认标记"

    config_version: str = Field(
        default="0.1.0",
        description="配置版本号，由插件维护，请勿手动修改",
    )
    enabled: bool = Field(
        default=True,
        description="是否启用回复标记",
        json_schema_extra={"label": "启用插件"},
    )
    replace_quote: bool = Field(
        default=True,
        description=(
            "开启后只用表情回应标记回复目标，不再插入灰色引用框；"
            "关闭则两者都保留（引用框 + 表情回应同时出现）"
        ),
        json_schema_extra={"label": "用表情替代引用框"},
    )
    emoji_id: str = Field(
        default=DEFAULT_EMOJI_ID,
        description=(
            "表情回应 ID。2705 是 ✅；想要别的表情就填对应 ID（如 128077 是 👍）。"
            "ID 填错时接口可能仍返回成功但不显示，改动后先发一条试试"
        ),
        json_schema_extra={"label": "表情 ID"},
    )
    host: str = Field(
        default="127.0.0.1",
        description="OneBot v11 HTTP 服务地址；本机走 SnowLuma 网关时保持 127.0.0.1",
        json_schema_extra={"label": "服务地址"},
    )
    port: int = Field(
        default=DEFAULT_PORT,
        description="OneBot v11 HTTP 服务端口；SnowLuma 默认 3000，NapCat 常见 9999",
        json_schema_extra={"label": "服务端口", "step": 1},
    )
    token: str = Field(
        default="",
        description=(
            "鉴权 Token。SnowLuma 严格校验「Authorization: Bearer <token>」格式，"
            "因此这里要连 Bearer 前缀一起填，例如「Bearer abc123」"
        ),
        json_schema_extra={"label": "鉴权 Token"},
    )
    timeout_seconds: int = Field(
        default=5,
        description="单次贴表情请求的超时时间（秒）；超时只记日志，不影响消息本身",
        json_schema_extra={"label": "请求超时（秒）", "step": 1},
    )


class ReplyConfirmConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)


class ReplyConfirmPlugin(MaiBotPlugin):
    """给被回复消息贴表情的插件。"""

    config_model = ReplyConfirmConfig

    def __init__(self) -> None:
        super().__init__()
        self._enabled: bool = True
        self._replace_quote: bool = True
        self._emoji_id: str = DEFAULT_EMOJI_ID
        self._host: str = "127.0.0.1"
        self._port: int = DEFAULT_PORT
        self._token: str = ""
        self._timeout_seconds: int = 5
        # 轻量统计，只进日志，不落盘。
        self._stripped_total: int = 0
        self._reacted_total: int = 0
        self._failed_total: int = 0
        self._last_error: str = ""

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def on_load(self) -> None:
        """插件加载时读取配置。"""

        self._refresh_settings()
        self._logger().info(
            "回复确认标记已加载：enabled=%s replace_quote=%s emoji_id=%s 网关=%s:%d",
            self._enabled,
            self._replace_quote,
            self._emoji_id,
            self._host,
            self._port,
        )

    async def on_unload(self) -> None:
        """插件卸载时把本次运行的统计写进日志，方便回查。"""

        self._logger().info(
            "回复确认标记已卸载：摘引用 %d 次、贴表情 %d 次、失败 %d 次%s",
            self._stripped_total,
            self._reacted_total,
            self._failed_total,
            f"，最近失败原因={self._last_error}" if self._last_error else "",
        )

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热重载时刷新内存副本。"""

        del config_data
        self._refresh_settings()
        self._logger().info(
            "回复确认标记配置已更新：scope=%s version=%s replace_quote=%s emoji_id=%s",
            scope,
            version,
            self._replace_quote,
            self._emoji_id,
        )

    # ------------------------------------------------------------------ #
    # 核心 Hook
    # ------------------------------------------------------------------ #
    @HookHandler(
        "send_service.before_send",
        name="strip_quote_box",
        description="把引用框摘掉，改由回复目标上的表情回应来标记",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def strip_quote_box(self, **kwargs: Any) -> dict[str, Any]:
        """在插入引用组件之前关掉引用，reply_message_id 原样保留给 after_send。"""

        try:
            if not self._enabled or not self._replace_quote:
                return {"action": "continue"}
            if not kwargs.get("set_reply"):
                return {"action": "continue"}
            reply_message_id = str(kwargs.get("reply_message_id") or "").strip()
            if not reply_message_id:
                # 没有引用目标就没有可标记的对象，保持宿主原有行为。
                return {"action": "continue"}
            modified_kwargs = dict(kwargs)
            modified_kwargs["set_reply"] = False
            self._stripped_total += 1
            return {"action": "continue", "modified_kwargs": modified_kwargs}
        except Exception as exc:  # noqa: BLE001 - 摘引用框失败必须放行，不能阻断发送
            self._logger().warning("摘引用框异常，本次保持原样：%s", exc)
            return {"action": "continue"}

    @HookHandler(
        "send_service.after_send",
        name="react_reply_target",
        description="给被回复的消息贴表情回应",
        mode=HookMode.OBSERVE,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def react_reply_target(self, **kwargs: Any) -> None:
        """平台确认发送成功后，在被回复的消息上贴表情。"""

        try:
            if not self._enabled:
                return
            if not kwargs.get("sent"):
                return
            reply_message_id = str(kwargs.get("reply_message_id") or "").strip()
            if not reply_message_id:
                return
            # HTTP 调用是阻塞的，放到线程里，避免占住事件循环。
            ok, detail = await asyncio.to_thread(self._set_emoji_like, reply_message_id)
            if ok:
                self._reacted_total += 1
                self._logger().debug("已给消息 %s 贴 %s", reply_message_id, self._emoji_id)
            else:
                self._failed_total += 1
                self._last_error = detail
                self._logger().warning(
                    "贴表情失败 message_id=%s emoji_id=%s 原因=%s",
                    reply_message_id,
                    self._emoji_id,
                    detail,
                )
        except Exception as exc:  # noqa: BLE001 - 贴表情失败不能影响已发送的消息
            self._failed_total += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._logger().warning("贴表情异常：%s", exc)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _refresh_settings(self) -> None:
        """把配置复制成实例字段，Hook 里不再反复穿透 self.config。"""

        section = self.config.plugin
        self._enabled = bool(section.enabled)
        self._replace_quote = bool(section.replace_quote)
        self._emoji_id = str(section.emoji_id).strip() or DEFAULT_EMOJI_ID
        self._host = str(section.host).strip() or "127.0.0.1"
        self._port = int(section.port)
        self._token = str(section.token or "").strip()
        self._timeout_seconds = max(1, int(section.timeout_seconds))

    def _set_emoji_like(self, message_id: str) -> tuple[bool, str]:
        """调用 OneBot 的 set_msg_emoji_like。返回 (是否成功, 详情)。"""

        connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout_seconds)
        payload = json.dumps(
            {"message_id": message_id, "emoji_id": self._emoji_id, "set": True}
        )
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = self._token
        try:
            connection.request("POST", "/set_msg_emoji_like", payload, headers)
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return False, f"响应不是 JSON（HTTP {response.status}）：{body[:200]}"
            if data.get("status") == "ok":
                return True, ""
            reason = data.get("wording") or data.get("message") or body[:200]
            return False, str(reason)
        finally:
            connection.close()

    def _logger(self) -> Any:
        """优先用宿主注入的 ctx.logger，未注入时退回标准 Logger。"""

        try:
            return self.ctx.logger
        except Exception:  # noqa: BLE001 - ctx 可能尚未注入
            return _FALLBACK_LOGGER


def create_plugin() -> ReplyConfirmPlugin:
    """插件入口。"""

    return ReplyConfirmPlugin()
