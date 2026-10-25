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

**私聊不贴**：QQ 不支持给私聊消息点表情，OneBot 会回
``emoji reactions are not supported on private messages``。这是平台能力缺失而非故障，
所以私聊一律静默跳过 —— 不发请求、不打日志、不计失败。

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

from pathlib import Path
from typing import Any, Literal
import asyncio
import http.client
import importlib.util
import json
import logging
import random
import sys

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder
from pydantic import field_validator

__all__ = [
    "EmojiSection",
    "GroupRule",
    "GroupSection",
    "NetworkSection",
    "PluginSection",
    "ReplyMarkConfig",
    "ReplyMarkPlugin",
    "create_plugin",
]

#: 插件 ID，必须与 ``_manifest.json`` 的 ``id`` 一致。
#: 注意宿主校验规则 ``^[A-Za-z0-9_]+(?:[.-][A-Za-z0-9_]+)+$``：必须含一个点或横线，
#: 纯下划线的 ID（如 ``reply_mark``）会被判定非法、加载失败。
#: 这个值决定插件状态与统计的归属，发布后**不要改动**。
PLUGIN_ID = "babyQ.maibot_reply-mark"

#: 「对的对的」（黄脸举绿勾牌）是 QQ 的系统表情，face id 为 478。
#: 查任意表情 id 的办法：用 OneBot 的 /get_msg 取那条消息，看 segments 里 face 段的 "id"。
DEFAULT_EMOJI_ID = "478"

#: 表情 CQ 码 / face id 大全（写进配置说明，方便直接查）。
CQ_CODE_DOC_URL = "https://docs.go-cqhttp.org/cqcode/"

#: SnowLuma / NapCat 的 OneBot v11 HTTP 默认端口。
DEFAULT_PORT = 3000

#: 让 WebUI 把这个字段渲染成滑条（前端按 ui_type 分支，SDK 侧读 x-widget）。
_SLIDER = {"x-widget": "slider"}

#: 「本群策略」的三个取值。
#: 中文注释必须揉进**值本身**：配置页的下拉框由前端 ``pn`` 组件渲染，
#: 选项文本就是 ``String(choice)``，前端不做 label 映射（``item_fields`` 里也没有
#: 存放显示名的位置），写在 description / hint 里都不会显示出来。
MODE_INHERIT = "inherit（沿用全局）"
MODE_CUSTOM = "custom（自定义）"
MODE_OFF = "off（不贴表情）"

#: 兼容层：手改 TOML 或旧版本配置里裸写的英文值，校验前统一补上中文注释。
_MODE_ALIASES = {
    "inherit": MODE_INHERIT,
    "custom": MODE_CUSTOM,
    "off": MODE_OFF,
}

#: 私聊场景下 OneBot 的固定错误文案（``emoji reactions are not supported on private messages``）。
#: QQ 本身不支持给私聊消息贴表情，属能力缺失而非故障，一律静默跳过。
_PRIVATE_UNSUPPORTED = "not supported on private"

_FALLBACK_LOGGER = logging.getLogger(PLUGIN_ID)


def _extract_group_id(message: Any) -> str:
    """从 after_send 的载荷里取群号，私聊返回空串。

    ``after_send`` 的 ``message`` 是 ``serialize_session_message()`` 的产物，
    群号位置：``message["message_info"]["group_info"]["group_id"]``；
    私聊时 ``group_info`` 为 ``None``。

    Args:
        message: Hook 载荷里的消息字典。

    Returns:
        str: 群号；私聊或结构不符时为空串。
    """

    if not isinstance(message, dict):
        return ""
    info = message.get("message_info")
    if not isinstance(info, dict):
        return ""
    group = info.get("group_info")
    if not isinstance(group, dict):
        return ""
    return str(group.get("group_id") or "").strip()


def _load_injector() -> Any:
    """按文件路径加载同目录的 ``webui_inject``。

    不用 ``from . import webui_inject``：宿主加载插件的方式不一定是标准包导入，
    相对导入有翻车风险，按路径加载最稳。
    """

    path = Path(__file__).resolve().parent / "webui_inject.py"
    spec = importlib.util.spec_from_file_location("_mb_webui_inject", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"加载 webui_inject.py 失败：{path}")
    module = importlib.util.module_from_spec(spec)
    # 先登记再执行：模块里有 @dataclass 之类的装饰器时，要靠 sys.modules 反查注解。
    sys.modules["_mb_webui_inject"] = module
    spec.loader.exec_module(module)
    return module


class PluginSection(PluginConfigBase):
    """贴表情的主开关与频率。引用框不受影响。"""

    __ui_label__ = "贴表情"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="关掉之后，回复消息不会再贴任何表情",
        json_schema_extra={"label": "启用插件", "order": 0},
    )
    react_probability: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "1 = 每次回复都贴，0 = 完全不贴，中间值按概率。"
            "没抽中时什么都不做，引用框与消息内容完全不受影响"
        ),
        json_schema_extra={
            "label": "贴表情概率",
            "step": 0.05,
            "order": 1,
            "hint": "0 完全不贴 · 1 每次都贴",
            **_SLIDER,
        },
    )
    emoji_count: int = Field(
        default=1,
        ge=1,
        le=10,
        description="每次贴几个表情；从表情池里随机抽，池子不够时按池子大小封顶",
        json_schema_extra={
            "label": "每次贴几个",
            "step": 1,
            "order": 2,
            "hint": "随机抽取且不重复，填 1 就是只贴一个",
            **_SLIDER,
        },
    )
    config_version: str = Field(
        default="0.4.0",
        description="配置版本号，由插件维护，请勿手动修改",
        json_schema_extra={"hidden": True},
    )
    beautify_ui: bool = Field(
        default=True,
        description=(
            "把插件配置页变成分组卡片、滑条数值可直接输入。"
            "拨完必须重载整个 WebUI 页面（浏览器 F5）才生效，"
            "在页面里刷新插件列表或切换插件都不算；关掉就还原成 dashboard 默认样式"
        ),
        json_schema_extra={
            "label": "美化配置页",
            "order": 3,
            "hint": "拨完要重载整个 WebUI 页面（F5）才生效，光刷新插件列表不行。dashboard 升级后会自动补回来",
        },
    )


class EmojiSection(PluginConfigBase):
    """每次发送从这些表情里随机抽。填的是 QQ 表情的 face id。"""

    __ui_label__ = "表情池"
    __ui_order__ = 1

    emoji_pool: list[str] = Field(
        default_factory=lambda: [DEFAULT_EMOJI_ID],
        description=(
            "QQ 表情的 face id（十进制），可填多个。"
            "478=[对的对的] 14=微笑 13=呲牙 76=赞 66=爱心 124=OK 182=笑哭。"
            "完整对照见 " + CQ_CODE_DOC_URL
        ),
        json_schema_extra={
            "label": "表情池",
            "order": 0,
            "hint": "CQ 码 / face id 大全：docs.go-cqhttp.org/cqcode · 不要填 2705 这类十六进制值（会贴出空白）",
        },
    )
    emoji_id: str = Field(
        default=DEFAULT_EMOJI_ID,
        description="表情池为空时用它兜底，一般不用管",
        json_schema_extra={"label": "备用表情 ID", "order": 1, "hint": "仅在表情池为空时生效"},
    )


class GroupRule(PluginConfigBase):
    """单个群的独立设置。没写进列表的群一律走全局。

    注意：配置页把「列表里的子模型」渲染成可增删的小卡片，但子模型字段
    只显示 ``label``、不显示 ``hint``，所以说明都揉进 label 里；另外
    ``int`` 在这条渲染路径上会退化成文本框，数字字段一律用 ``float`` 声明。
    """

    group_id: str = Field(
        default="",
        description="QQ 群号（纯数字）",
        json_schema_extra={"label": "群号"},
    )
    mode: Literal[
        "inherit（沿用全局）", "custom（自定义）", "off（不贴表情）"
    ] = Field(
        default=MODE_INHERIT,
        description="沿用全局=跟上面的全局配置走；自定义=用本群下面几项；不贴=这个群完全不贴",
        json_schema_extra={"label": "本群策略"},
    )
    react_probability: float = Field(
        default=0.6,
        description="本群贴表情概率，0~1。仅「本群策略=自定义」时生效",
        json_schema_extra={"label": "概率 0~1（仅自定义）"},
    )
    emoji_count: float = Field(
        default=1.0,
        description="本群每次贴几个。仅「本群策略=自定义」时生效",
        json_schema_extra={"label": "贴几个（仅自定义）"},
    )
    emoji_pool: list[str] = Field(
        default_factory=list,
        description="本群表情池（face id）。留空则沿用全局表情池",
        json_schema_extra={"label": "表情池（空=全局）"},
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> Any:
        """把裸写的 ``inherit`` / ``custom`` / ``off`` 补成带中文注释的正式值。

        这样手改 TOML、或升级前写下的旧配置都不会因为取值不匹配而让插件加载失败。

        Args:
            value: 配置里读到的原始取值。

        Returns:
            Any: 归一化后的取值；无法识别时原样返回，交给 Literal 报错。
        """

        if isinstance(value, str):
            key = value.split("（")[0].split("(")[0].strip().lower()
            return _MODE_ALIASES.get(key, value)
        return value


class GroupSection(PluginConfigBase):
    """给指定群单独设规矩，没写到的群走全局，互不影响。"""

    __ui_label__ = "分群设置"
    __ui_order__ = 2

    rules: list[GroupRule] = Field(
        default_factory=list,
        description=(
            "写到的群按本群设置运行，没写到的群走全局配置；"
            "同一个群写了两条时以第一条为准"
        ),
        json_schema_extra={
            "label": "分群规则",
            "order": 0,
            "hint": "点「添加项目」加一个群。群号填错只会被当成没配置，不影响别的群",
        },
    )


class _ResolvedRule:
    """分群配置解析后的运行时形态。

    刻意用普通类而不是 ``@dataclass``：本文件有 ``from __future__ import annotations``，
    ``dataclass`` 处理装饰器时要靠 ``sys.modules[cls.__module__]`` 反查注解，
    一旦加载方没把模块注册进 ``sys.modules``（宿主目前有注册，但这属于隐式依赖），
    就会在 import 阶段直接抛 ``AttributeError``。普通类不吃这一套。
    """

    __slots__ = ("enabled", "probability", "count", "pool")

    def __init__(self, enabled: bool, probability: float, count: int, pool: list[str]) -> None:
        #: 这个群是否贴表情（``off`` 时为 False）。
        self.enabled = enabled
        #: 贴表情概率。
        self.probability = probability
        #: 每次贴几个。
        self.count = count
        #: 表情池，已去重；为空表示沿用全局。
        self.pool = pool


class NetworkSection(PluginConfigBase):
    """OneBot v11 HTTP 连接信息。本机走 SnowLuma 网关时保持默认即可。"""

    __ui_label__ = "连接"
    __ui_order__ = 3

    host: str = Field(
        default="127.0.0.1",
        description="OneBot v11 HTTP 服务地址",
        json_schema_extra={"label": "服务地址", "order": 0, "hint": "本机 SnowLuma 网关保持 127.0.0.1"},
    )
    port: int = Field(
        default=DEFAULT_PORT,
        description="OneBot v11 HTTP 服务端口",
        json_schema_extra={
            "label": "服务端口",
            "step": 1,
            "order": 1,
            "hint": "SnowLuma 默认 3000；NapCat 常见 9999",
        },
    )
    token: str = Field(
        default="",
        description="鉴权 Token，要连 Bearer 前缀一起填",
        json_schema_extra={"label": "鉴权 Token", "order": 2, "hint": "格式：Bearer xxxx（SnowLuma 严格校验前缀）"},
    )
    timeout_seconds: int = Field(
        default=5,
        description="单次贴表情请求的超时时间（秒）",
        json_schema_extra={
            "label": "请求超时（秒）",
            "step": 1,
            "order": 3,
            "hint": "超时只记日志，不影响消息本身",
        },
    )


class ReplyMarkConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    emoji: EmojiSection = Field(default_factory=EmojiSection)
    group: GroupSection = Field(default_factory=GroupSection)
    network: NetworkSection = Field(default_factory=NetworkSection)


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
        self._beautify_ui: bool = True
        # 群号 → 本群策略；空字典表示所有群都走全局。
        self._group_rules: dict[str, _ResolvedRule] = {}
        # 轻量统计，只进日志，不落盘。
        self._reacted_total: int = 0
        self._skipped_total: int = 0
        self._skipped_private_total: int = 0
        self._skipped_off_total: int = 0
        self._failed_total: int = 0
        self._last_error: str = ""

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def on_load(self) -> None:
        """插件加载时读取配置。"""

        self._refresh_settings()
        self._logger().info(
            "回复标记插件已加载：enabled=%s 概率=%s 表情池=%s 每个回%d 网关=%s:%d 分群规则=%d 条（引用框不摘）",
            self._enabled,
            self._react_probability,
            self._emoji_pool,
            self._emoji_count,
            self._host,
            self._port,
            len(self._group_rules),
        )
        if self._group_rules:
            for group_id, rule in self._group_rules.items():
                self._logger().info(
                    "  分群 %s：%s",
                    group_id,
                    "不贴表情"
                    if not rule.enabled
                    else f"概率={rule.probability} 每个回{rule.count} 表情池={rule.pool or '沿用全局'}",
                )
        await self._sync_webui_skin()

    async def on_unload(self) -> None:
        """插件卸载时把本次运行的统计写进日志，方便回查。"""

        self._logger().info(
            "回复标记插件已卸载：贴表情 %d 次、抽未中 %d 次、私聊跳过 %d 次、分群关闭 %d 次、失败 %d 次%s",
            self._reacted_total,
            self._skipped_total,
            self._skipped_private_total,
            self._skipped_off_total,
            self._failed_total,
            f"，最近失败原因={self._last_error}" if self._last_error else "",
        )

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热重载时刷新内存副本。"""

        del config_data
        self._refresh_settings()
        self._logger().info(
            "回复标记插件配置已更新：scope=%s version=%s 概率=%s 表情池=%s 每个回%d 分群规则=%d 条",
            scope,
            version,
            self._react_probability,
            self._emoji_pool,
            self._emoji_count,
            len(self._group_rules),
        )
        await self._sync_webui_skin()

    async def _sync_webui_skin(self) -> None:
        """按「美化配置页」开关注入或移除样式。失败只记日志，绝不影响贴表情。"""

        try:
            injector = await asyncio.to_thread(_load_injector)
            dist = injector.find_dist()
            if dist is None:
                if self._beautify_ui:
                    self._logger().debug("配置页美化：没找到 maibot_dashboard，跳过")
                return
            if self._beautify_ui:
                changed, message = await asyncio.to_thread(injector.inject, dist)
            else:
                changed, message = await asyncio.to_thread(injector.remove, dist)
            self._logger().info("配置页美化：%s（开关=%s）", message, self._beautify_ui)
            del changed
        except Exception as exc:  # noqa: BLE001 - 美化失败不能连累主功能
            self._logger().warning("配置页美化同步失败（不影响贴表情）：%s", exc)

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
            # 私聊不贴：QQ 不支持给私聊消息点表情，OneBot 会返回
            # "emoji reactions are not supported on private messages"。
            # 这是平台能力缺失，不是故障 —— 静默跳过，不打日志、不计失败。
            group_id = _extract_group_id(kwargs.get("message"))
            if not group_id:
                self._skipped_private_total += 1
                return
            # 分群策略优先：写到的群按本群设置跑，没写到的群回落全局。
            rule = self._group_rules.get(group_id)
            if rule is None:
                probability = self._react_probability
                pool = self._emoji_pool
                count = self._emoji_count
            elif not rule.enabled:
                self._skipped_off_total += 1
                return
            else:
                probability = rule.probability
                pool = rule.pool or self._emoji_pool
                count = rule.count
            if probability <= 0:
                self._skipped_total += 1
                return
            if probability < 1.0 and random.random() > probability:
                self._skipped_total += 1
                return
            emojis = self._draw_emojis(pool, count)
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
                # 兜底：万一群号没取到、真的打到私聊去了，也按能力缺失静默处理。
                if _PRIVATE_UNSUPPORTED in detail.lower():
                    self._skipped_private_total += 1
                    return
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

        main = self.config.plugin
        emoji = self.config.emoji
        network = self.config.network
        self._enabled = bool(main.enabled)
        self._react_probability = min(1.0, max(0.0, float(main.react_probability)))
        self._emoji_count = max(1, int(main.emoji_count))
        self._emoji_id = str(emoji.emoji_id).strip() or DEFAULT_EMOJI_ID
        pool = [str(item).strip() for item in (emoji.emoji_pool or [])]
        # 去重并保留顺序；池子为空时退回备用表情 ID。
        self._emoji_pool = list(dict.fromkeys(item for item in pool if item)) or [self._emoji_id]
        self._host = str(network.host).strip() or "127.0.0.1"
        self._port = int(network.port)
        self._token = str(network.token or "").strip()
        self._timeout_seconds = max(1, int(network.timeout_seconds))
        self._beautify_ui = bool(main.beautify_ui)
        self._group_rules = self._parse_group_rules(getattr(self.config, "group", None))

    def _parse_group_rules(self, section: Any) -> dict[str, _ResolvedRule]:
        """把分群配置整理成 ``{群号: 策略}``。

        宽容策略：群号没填、重复、以及 ``mode=inherit`` 的行都不会登记
        （不登记即回落全局），所以某一处写错不会连累其它群，也不会让插件起不来。

        Args:
            section: ``GroupSection`` 配置对象，可能为 ``None``。

        Returns:
            Dict[str, _ResolvedRule]: 群号到策略的映射；空字典表示全走全局。
        """

        resolved: dict[str, _ResolvedRule] = {}
        rules = getattr(section, "rules", None) or []
        for index, rule in enumerate(rules, start=1):
            group_id = str(getattr(rule, "group_id", "") or "").strip()
            if not group_id:
                continue
            if group_id in resolved:
                self._logger().debug(
                    "分群规则第 %d 条的群号 %s 与前面重复，按先出现的生效", index, group_id
                )
                continue
            # 取值形如「custom（自定义）」，取出括号前的英文关键词做判断。
            mode = str(getattr(rule, "mode", "") or "").split("（")[0].split("(")[0].strip().lower()
            if mode == "off":
                resolved[group_id] = _ResolvedRule(False, 0.0, 1, [])
                continue
            if mode != "custom":
                # inherit（默认值）→ 不登记，自然回落全局。
                continue
            raw_probability = getattr(rule, "react_probability", 0.0)
            raw_count = getattr(rule, "emoji_count", 1)
            raw_pool = getattr(rule, "emoji_pool", None) or []
            probability = min(1.0, max(0.0, float(raw_probability or 0.0)))
            count = min(10, max(1, int(round(float(raw_count or 1)))))
            pool = list(
                dict.fromkeys(str(item).strip() for item in raw_pool if str(item).strip())
            )
            resolved[group_id] = _ResolvedRule(True, probability, count, pool)
        return resolved

    def _draw_emojis(self, pool: list[str], count: int) -> list[str]:
        """从给定表情池随机抽 count 个（不放回，池子不够就全给）。"""

        candidates = pool or [DEFAULT_EMOJI_ID]
        pick = min(max(1, int(count)), len(candidates))
        if pick >= len(candidates):
            return list(candidates)
        return random.sample(candidates, pick)

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
            reason_text = str(reason)
            # 同一条消息被回第二次时，QQ 会报「已经设置过该表情」。
            # 这属于幂等成功（表情本来就在上面），不该算失败刷日志。
            if "已经设置过" in reason_text:
                return True, ""
            return False, reason_text
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
