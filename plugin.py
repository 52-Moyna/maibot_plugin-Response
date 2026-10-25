"""回复标记插件（Reply Mark）—— MaiBot 插件。

机器人在回复某条消息时，**额外**在被回复的那条消息上贴表情回应（默认「对的对的」），
让人一眼看出它在回谁。引用框照原样保留 —— 贴表情只是附加信息，不替掉任何东西。

实现只有一个钩子：

- ``send_service.after_send``：平台确认发送成功后触发，带 ``sent`` 与 ``reply_message_id``。
  这里按 ``react_probability`` 掷骰子、从 ``emoji_pool`` 抽 ``emoji_count`` 个表情，
  逐个调 OneBot 的 ``set_msg_emoji_like``。

因为不改任何发送参数（不碰 ``set_reply``），所以：

- 引用框、消息内容、发送链路全部保持宿主原样；
- 插件出问题最多是「没贴上表情」，不可能影响消息本身。

表情 ID 说明（``emoji_pool`` 里填的是 QQ 表情的 **face id**，十进制）：

    478 = 对的对的   14 = 微笑   13 = 呲牙   76 = 赞
     66 = 爱心      124 = OK     182 = 笑哭    32 = 疑问

⚠️ 不要填 Unicode 码点的十六进制字面值（例如 ``2705`` 想表示 ✅）：接口会返回
``status: ok``，但客户端贴出来是空白。要验证真贴上了，用 OneBot 的
``/get_emoji_likes`` 查那条消息。

表情 CQ 码 / face id 大全：https://docs.go-cqhttp.org/cqcode/

查任意表情真实 id 的办法：用 ``/get_msg`` 取一条带该表情的消息，看 segments 里
``face`` 段的 ``"id"``。

宿主契约（MaiBot 1.0.0+ / maibot-plugin-sdk 2.x）：

- ``after_send`` 以 ``HookMode.OBSERVE`` 注册，只观察、不回传修改；
- 任一环节失败都只记日志、放行，绝不影响消息发送本身。
"""

from __future__ import annotations

from typing import Any
import asyncio
import http.client
import json
import logging
import random

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

__all__ = [
    "PluginSection",
    "ReplyMarkConfig",
    "ReplyMarkPlugin",
    "create_plugin",
]

#: 插件 ID。这个值决定插件状态与配置文件的归属，**不要改动**。
PLUGIN_ID = "workbuddy.reply_confirm"

#: 「对的对的」（黄脸举绿勾牌）是 QQ 的系统表情，face id 为 478。
#: 查任意表情 id 的办法：用 OneBot 的 /get_msg 取那条消息，看 segments 里 face 段的 "id"。
DEFAULT_EMOJI_ID = "478"

#: 表情 CQ 码 / face id 大全（写进配置说明，方便直接查）。
CQ_CODE_DOC_URL = "https://docs.go-cqhttp.org/cqcode/"

#: SnowLuma / NapCat 的 OneBot v11 HTTP 默认端口。
DEFAULT_PORT = 3000

#: 让 WebUI 把这个字段渲染成滑条（前端按 ui_type 分支，SDK 侧读 x-widget）。
_SLIDER = {"x-widget": "slider"}

_FALLBACK_LOGGER = logging.getLogger(PLUGIN_ID)


class PluginSection(PluginConfigBase):
    """插件主体配置。"""

    __ui_label__ = "回复标记插件"

    config_version: str = Field(
        default="0.3.0",
        description="配置版本号，由插件维护，请勿手动修改",
    )
    enabled: bool = Field(
        default=True,
        description="是否启用回复标记",
        json_schema_extra={"label": "启用插件"},
    )
    react_probability: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "每次回复贴表情的概率。1=每次都贴，0=完全不贴。"
            "贴表情只是附加动作，没抽中时引用框和消息内容完全不受影响"
        ),
        json_schema_extra={"label": "贴表情概率", "step": 0.05, **_SLIDER},
    )
    emoji_pool: list[str] = Field(
        default_factory=lambda: [DEFAULT_EMOJI_ID],
        description=(
            "表情池，填 QQ 表情的 face id（十进制），可填多个，每次从池里随机抽。"
            "478=[对的对的]、14=微笑、13=呲牙、76=赞、66=爱心、124=OK、182=笑哭。"
            "CQ 码与 face id 大全见 " + CQ_CODE_DOC_URL + "；"
            "查某个表情的真实 id 用 /get_msg 看 segments 里 face 段的 id。"
            "不要填 Unicode 十六进制值（如 2705），会贴出空白"
        ),
        json_schema_extra={"label": "表情池"},
    )
    emoji_count: int = Field(
        default=1,
        ge=1,
        le=10,
        description=(
            "每次贴几个表情。从表情池里随机抽取，池子不够时按池子大小封顶；"
            "填 1 就是只贴一个"
        ),
        json_schema_extra={"label": "每次贴几个", "step": 1, **_SLIDER},
    )
    emoji_id: str = Field(
        default=DEFAULT_EMOJI_ID,
        description="备用表情 ID：表情池为空时用这个。一般不用管，配表情池就够了",
        json_schema_extra={"label": "备用表情 ID"},
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


class ReplyMarkConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)


class ReplyMarkPlugin(MaiBotPlugin):
    """给被回复消息贴表情的插件（纯附加，不动引用框）。"""

    config_model = ReplyMarkConfig

    def __init__(self) -> None:
        super().__init__()
        self._enabled: bool = True
        self._react_probability: float = 1.0
        self._emoji_pool: list[str] = [DEFAULT_EMOJI_ID]
        self._emoji_count: int = 1
        self._emoji_id: str = DEFAULT_EMOJI_ID
        self._host: str = "127.0.0.1"
        self._port: int = DEFAULT_PORT
        self._token: str = ""
        self._timeout_seconds: int = 5
        # 轻量统计，只进日志，不落盘。
        self._reacted_total: int = 0
        self._skipped_total: int = 0
        self._failed_total: int = 0
        self._last_error: str = ""

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def on_load(self) -> None:
        """插件加载时读取配置。"""

        self._refresh_settings()
        self._logger().info(
            "回复标记插件已加载：enabled=%s 概率=%s 表情池=%s 每个回%d 网关=%s:%d（引用框不摘）",
            self._enabled,
            self._react_probability,
            self._emoji_pool,
            self._emoji_count,
            self._host,
            self._port,
        )

    async def on_unload(self) -> None:
        """插件卸载时把本次运行的统计写进日志，方便回查。"""

        self._logger().info(
            "回复标记插件已卸载：贴表情 %d 次、抽未中 %d 次、失败 %d 次%s",
            self._reacted_total,
            self._skipped_total,
            self._failed_total,
            f"，最近失败原因={self._last_error}" if self._last_error else "",
        )

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热重载时刷新内存副本。"""

        del config_data
        self._refresh_settings()
        self._logger().info(
            "回复标记插件配置已更新：scope=%s version=%s 概率=%s 表情池=%s 每个回%d",
            scope,
            version,
            self._react_probability,
            self._emoji_pool,
            self._emoji_count,
        )

    # ------------------------------------------------------------------ #
    # 核心 Hook
    # ------------------------------------------------------------------ #
    @HookHandler(
        "send_service.after_send",
        name="mark_after_send",
        description="给被回复的消息贴表情回应（引用框保持不变）",
        mode=HookMode.OBSERVE,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def mark_after_send(self, **kwargs: Any) -> None:
        """平台确认发送成功后，在被回复的消息上贴表情。"""

        try:
            if not self._enabled:
                return
            if not kwargs.get("sent"):
                return
            reply_message_id = str(kwargs.get("reply_message_id") or "").strip()
            if not reply_message_id:
                return
            if self._react_probability <= 0:
                self._skipped_total += 1
                return
            if self._react_probability < 1.0 and random.random() > self._react_probability:
                self._skipped_total += 1
                return
            emojis = self._draw_emojis()
            # HTTP 调用是阻塞的，放到线程里，避免占住事件循环。
            ok_count, detail = await asyncio.to_thread(
                self._set_emoji_likes, reply_message_id, emojis
            )
            if ok_count:
                self._reacted_total += 1
                self._logger().debug(
                    "已给消息 %s 贴 %s（成功 %d/%d）",
                    reply_message_id,
                    emojis,
                    ok_count,
                    len(emojis),
                )
            if ok_count < len(emojis):
                self._failed_total += 1
                self._last_error = detail
                self._logger().warning(
                    "贴表情部分失败 message_id=%s emojis=%s 成功=%d/%d 原因=%s",
                    reply_message_id,
                    emojis,
                    ok_count,
                    len(emojis),
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
        self._react_probability = min(1.0, max(0.0, float(section.react_probability)))
        self._emoji_count = max(1, int(section.emoji_count))
        self._emoji_id = str(section.emoji_id).strip() or DEFAULT_EMOJI_ID
        pool = [str(item).strip() for item in (section.emoji_pool or [])]
        # 去重并保留顺序；池子为空时退回备用表情 ID。
        self._emoji_pool = list(dict.fromkeys(item for item in pool if item)) or [self._emoji_id]
        self._host = str(section.host).strip() or "127.0.0.1"
        self._port = int(section.port)
        self._token = str(section.token or "").strip()
        self._timeout_seconds = max(1, int(section.timeout_seconds))

    def _draw_emojis(self) -> list[str]:
        """从表情池随机抽 emoji_count 个（不放回，池子不够就全给）。"""

        pool = self._emoji_pool or [DEFAULT_EMOJI_ID]
        count = min(max(1, self._emoji_count), len(pool))
        if count >= len(pool):
            return list(pool)
        return random.sample(pool, count)

    def _set_emoji_likes(self, message_id: str, emojis: list[str]) -> tuple[int, str]:
        """逐个调用 set_msg_emoji_like，返回 (成功个数, 最后一条错误详情)。"""

        ok_count = 0
        detail = ""
        for emoji_id in emojis:
            ok, reason = self._set_emoji_like(message_id, emoji_id)
            if ok:
                ok_count += 1
            else:
                detail = f"emoji_id={emoji_id}：{reason}"
        return ok_count, detail

    def _set_emoji_like(self, message_id: str, emoji_id: str) -> tuple[bool, str]:
        """调用 OneBot 的 set_msg_emoji_like（单次单个表情）。返回 (是否成功, 详情)。"""

        connection = http.client.HTTPConnection(
            self._host, self._port, timeout=self._timeout_seconds
        )
        payload = json.dumps({"message_id": message_id, "emoji_id": emoji_id, "set": True})
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


def create_plugin() -> ReplyMarkPlugin:
    """插件入口。"""

    return ReplyMarkPlugin()
