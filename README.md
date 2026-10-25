# 回复标记插件（Reply Mark）

机器人回复某条消息时，**额外在被回复的那条消息上贴表情回应**（默认 `[对的对的]`），群里一眼就能看出它在回谁。

## 定位：只加标记，不替东西

引用框**原样保留**。贴表情是附加信息，不是替代品——起因是「回复某条消息」这件事在群里不够显眼，加一个表情回应等于给别人一个提示：我回的是这条。

所以插件不碰 `set_reply`、不改消息内容、不改发送参数。它坏了最多是「没贴上表情」，不可能影响消息本身。

## 行为

| 配置 | 效果 |
|---|---|
| `react_probability = 1.0`（默认） | 每次都贴 |
| `react_probability = 0.5` | 一半概率贴 |
| `react_probability = 0` | 完全不贴 |

引用框、消息内容、发送链路在任何取值下都不变。没抽中 = 什么都不发生，不是「摘掉标记」。

## 表情池与多表情

```toml
emoji_pool = ["478", "14", "76"]   # 每次从池里随机抽
emoji_count = 1                    # 每次贴几个
```

- 池子里可填任意多个 face id，每次发送随机抽 `emoji_count` 个（不放回）。
- 池子比 `emoji_count` 小的时候按池子大小封顶，不会重复贴同一个。
- `emoji_count = 3` + 池子 3 个 = 三个全贴上；池子 5 个则每轮随机挑 3 个。

## WebUI 配置

`react_probability` 与 `emoji_count` 在 WebUI 里是**滑条**，靠字段声明 `json_schema_extra={"x-widget": "slider", "step": ...}` 实现：

- 仪表盘渲染字段时按 `ui_type` 分支（`plugin-config-*.js` 里 `switch(s.ui_type)` → `case "slider"`）；
- SDK 侧 `ui_type = json_extra.get("x-widget") or _default_ui_type(field_type)`（`maibot_sdk/config.py:324`）；
- 🔑 整数/浮点字段**不会**自动变滑条，必须显式给 `x-widget`。

## 原理

只挂一个钩子：

```python
# src/services/send_service.py
after_send  →  sent / reply_message_id   # 发送已成功，拿 ID 去贴表情
```

`after_send` 里掷骰子 → 抽表情 → 逐个调 OneBot 的 `set_msg_emoji_like`。HTTP 调用用 `asyncio.to_thread` 包住，不阻塞事件循环。

全程以 `HookMode.OBSERVE` 注册，不回传任何修改。

## 配置

```toml
[plugin]
enabled = true
config_version = "0.3.0"

react_probability = 1.0          # 0~1，滑条
emoji_pool = ["478"]             # 表情池，可多个
emoji_count = 1                  # 每次贴几个，滑条
emoji_id = "478"                 # 备用：池为空时用它

host = "127.0.0.1"
port = 3000                      # SnowLuma 网关；NapCat 常见 9999
token = "Bearer xxxx"            # SnowLuma 要求连 Bearer 前缀一起写
timeout_seconds = 5
```

### 表情 ID 怎么填

填 **QQ 表情的 face id**（就是消息里 `[xxx]` 那个表情的编号）：

| 表情 | face id |
|---|---|
| [对的对的]（黄脸举绿勾牌） | `478` |
| 微笑 | `14` |
| 呲牙 | `13` |
| 赞 | `76` |
| 爱心 | `66` |
| OK | `124` |
| 笑哭 | `182` |
| 疑问 | `32` |

**表情 CQ 码 / face id 大全**：<https://docs.go-cqhttp.org/cqcode/>

**想查任意表情的 id**：让群里出现一次那个表情，然后用 OneBot 的 `/get_msg` 取那条消息，看 segments 里 `type: "face"` 段的 `id` 字段：

```json
[{"type": "face", "data": {"id": "478", "resultId": ""}}]
```

那是唯一准确的办法。别去抄网上的对照表——社区流传的表互相矛盾（有张表说 32 是「对勾」，QQ 官方表里 32 其实是「疑问」）。

**另一个坑**：`set_msg_emoji_like` 对无效 ID 也返回 `status: ok`，光看返回值会被骗。验证要用 `get_emoji_likes` 查该消息的该 id，返回里出现 `user_id` 才算真贴上。

**还有**：填 `2705`、`128536` 这类 Unicode 十六进制字面值不行，会贴出空白。系统 emoji 要用码点的十进制值（✅ 是 `9989`），但表情包/系统表情用的是 face id，两套编号不通用。

## 依赖

- MaiBot 1.0.0+，maibot-plugin-sdk 2.5.1+
- 一个支持 `set_msg_emoji_like` 的 OneBot v11 HTTP 服务（SnowLuma / NapCat）

不需要任何额外 Python 包。

## 已知情况

- 与「麦麦贴表情」（`davidblackcn.maibot-message-react-plugin`）不冲突，但两者都会贴表情：那个是按概率主动贴别人的消息，本插件是确定性地贴「被回复的那条」。同群同时开会出现两套表情回应。
- 插件 ID 仍是 `workbuddy.reply_confirm`（改名会丢插件状态与已生成的配置），只有显示名是「回复标记插件」。
- v0.1.0/0.2.0 的「用表情替代引用框」（`replace_quote`）已在 0.3.0 移除：引用框不再被摘掉。
