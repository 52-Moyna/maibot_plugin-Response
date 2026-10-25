# 回复标记插件（Reply Mark）

机器人回复某条消息时，**自动在被回复的那条消息上贴表情回应**（默认 `[对的对的]`），群里一眼就能看出它在回谁。

## 解决什么问题

MaiBot 引用回复时，会在自己的消息上方挂一条灰色引用框。全天下来每条都挂，一屏全是灰块，观感像客服系统。

这个插件换成 QQ 原生的「表情回应」：一个表情挂在被回复的那条消息上，不占屏、不打断阅读，还能看出回的是哪条。

## 行为

| 配置 | 效果 |
|---|---|
| `replace_quote = true`（默认） | 只贴表情，**不显示引用框** |
| `replace_quote = false` | 引用框和表情都保留 |
| `react_probability = 1.0`（默认） | 每次都贴 |
| `react_probability = 0.5` | 一半概率贴 |
| `react_probability = 0` | 完全不贴（关闭贴表情） |

没有引用目标的消息（没被要求回复某条）不受影响，正常发送。

**概率没抽中时，本插件也不摘引用框**——只摘框不贴表情会让那条消息失去所有指向，等于凭空说话。所以「不贴」就等于「这条完全不介入」。

## 表情池与多表情

```toml
emoji_pool = ["478", "14", "76"]   # 每次从池里随机抽
emoji_count = 1                    # 每次贴几个
```

- 池子里可填任意多个 face id，每次发送随机抽 `emoji_count` 个（不放回）。
- 池子比 `emoji_count` 小的时候按池子大小封顶，不会重复贴同一个。
- `emoji_count = 3` + 池子 3 个 = 三个全贴上；池子 5 个则每轮随机挑 3 个。

## 原理

宿主在两个发送钩子上都提供了「被引用消息 ID」，且插入引用组件发生在 `before_send` 之后：

```python
# src/services/send_service.py
before_send  →  掷骰子 + 抽表情 + 可改写 set_reply，reply_message_id 不动
after_send   →  sent / reply_message_id                  # 拿 ID 去贴表情
```

插件在 `before_send` 里完成决策：抽中就把 `set_reply` 置 `False`（引用框不插入），把本次要贴的表情暂存起来；`after_send` 确认发送成功后取出暂存的表情，逐个调 OneBot 的 `set_msg_emoji_like`。

暂存按 `reply_message_id` 排队并带 60 秒过期，同一目标连续回多次也不会串。

全程不拦截、不改写消息内容；任一步失败只记日志，不影响消息发送。

## 配置

```toml
[plugin]
enabled = true
config_version = "0.2.0"

react_probability = 1.0          # 0~1，0 = 不贴
replace_quote = true

emoji_pool = ["478"]             # 表情池，可多个
emoji_count = 1                  # 每次贴几个
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
- 插件 ID 仍是 `workbuddy.reply_confirm`（改名会丢插件状态与已生成的配置），只有显示名改成「回复标记插件」。
