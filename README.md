# 回复确认标记（Reply Confirm）

机器人回复某条消息时，**自动在被回复的那条消息上贴一个表情回应**（默认 ✅），群里一眼就能看出它在回谁。

## 解决什么问题

MaiBot 引用回复时，会在自己的消息上方挂一条灰色引用框。全天下来每条都挂，一屏全是灰块，观感像客服系统。

这个插件换成 QQ 原生的「表情回应」：一个 ✅ 挂在被回复的那条消息上，不占屏、不打断阅读，还能看出回的是哪条。

## 行为

| 配置 | 效果 |
|---|---|
| `replace_quote = true`（默认） | 只贴表情，**不显示引用框** |
| `replace_quote = false` | 引用框和表情都保留 |

没有引用目标的消息（没被要求回复某条）不受影响，正常发送。

## 原理

宿主在两个发送钩子上都提供了「被引用消息 ID」，且插入引用组件发生在 `before_send` 之后：

```python
# src/services/send_service.py
before_send  →  可改写 set_reply，reply_message_id 不动   # 引用框不插入
after_send   →  sent / reply_message_id                  # 拿 ID 去贴表情
```

插件在 `before_send` 里只把 `set_reply` 置 `False`，`reply_message_id` 原样传给 `after_send`，再调 OneBot 的 `set_msg_emoji_like`。

全程不拦截、不改写消息内容；任一步失败只记日志，不影响消息发送。

## 配置

```toml
[plugin]
enabled = true
replace_quote = true
emoji_id = "2705"        # 2705 = ✅，128077 = 👍
host = "127.0.0.1"
port = 3000              # SnowLuma 网关；NapCat 常见 9999
token = "Bearer xxxx"    # SnowLuma 要求连 Bearer 前缀一起写
timeout_seconds = 5
```

改 `emoji_id` 后先发一条消息看效果：**ID 无效时接口可能仍返回 `status: ok`，但群里不显示**。

## 依赖

- MaiBot 1.0.0+，maibot-plugin-sdk 2.5.1+
- 一个支持 `set_msg_emoji_like` 的 OneBot v11 HTTP 服务（SnowLuma / NapCat）

不需要任何额外 Python 包。

## 已知情况

- 与「麦麦贴表情」（`davidblackcn.maibot-message-react-plugin`）不冲突，但两者都会贴表情：那个是按概率主动贴别人的消息，本插件是确定性地贴「被回复的那条」。同群同时开会出现两套表情回应。
