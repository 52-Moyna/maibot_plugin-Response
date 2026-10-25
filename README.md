# 回复标记插件

机器人回复某条消息时，顺手在被回复的那条消息上贴一个表情回应。

![插件配置界面](https://raw.githubusercontent.com/52-Moyna/maibot_plugin-Response/main/docs/config-panel.png)

`MaiBot 1.0.0+` · `maibot-plugin-sdk 2.5.1+` · 零第三方依赖 · Windows / Linux / macOS

> 🤖 **本插件完全由 AI（DeepSeek V4.1 Flash）生成。**  
> 踩到 bug、想加功能、或者觉得哪里写得不对，都欢迎去 [Issues](https://github.com/52-Moyna/maibot_plugin-Response/issues) 提。

---

## 功能

- **给被回复的消息贴表情** —— 默认 `[对的对的]`（`478`），原消息上直接挂一个表情回应
- **概率可调** —— 0~1 滑条，也能直接填数字，`0` 就是完全不贴
- **表情池随机抽** —— 可填多个 face id，一次贴 1~10 个，不重复

## 安装

1. 整个目录放到 `<MaiBot>/plugins/maibot_plugin-Response/`
2. `cp config.example.toml config.toml`，填上网关地址和 Token
3. 重启 MaiBot，日志出现 `回复标记插件已加载` 就成功了

### 可选：美化配置页

配置页默认是普通表单。跑一下这个，滑条就能直接填数字、整体变成分组卡片：

```bash
python tools/inject_webui.py
```

它就是往 dashboard 的静态文件里插一段 CSS/JS（框架不让插件改页面，只能这么绕）。升级 dashboard 后会失效，重跑一次即可；要在 MaiBot 用的那个 Python 环境里执行。

## 配置

WebUI 里是三张卡片，也可以直接改 `config.toml`。

| `[plugin]` 贴表情      | 默认     | 说明             |
| ------------------- | ------ | -------------- |
| `enabled`           | `true` | 总开关            |
| `react_probability` | `1.0`  | 贴表情概率，`0` 完全不贴 |
| `emoji_count`       | `1`    | 一次贴几个（1~10）    |

| `[emoji]` 表情池 | 默认        | 说明               |
| ------------- | --------- | ---------------- |
| `emoji_pool`  | `["478"]` | face id 列表，每次随机抽 |
| `emoji_id`    | `"478"`   | 池子为空时的兜底         |

| `[network]` 连接    | 默认                   | 说明                                              |
| ----------------- | -------------------- | ----------------------------------------------- |
| `host` / `port`   | `127.0.0.1` / `3000` | OneBot v11 HTTP 地址，NapCat 常见 `9999`             |
| `token`           | 空                    | 原样塞进 `Authorization` 头，SnowLuma 要写 `Bearer xxx` |
| `timeout_seconds` | `5`                  | 超时只记日志，不影响消息                                    |

## 表情 ID

填 QQ 表情的 **face id**，不是 Unicode 码点。

- 完整 **299 个（含 190 个新版）** → [qq-face-ids.md](https://github.com/52-Moyna/maibot_plugin-Response/blob/main/docs/qq-face-ids.md)，`Ctrl+F` 搜名字
- 想更顺手 → [打开速查器](https://52-moyna.github.io/maibot_plugin-Response/face-lookup.html)，搜索、筛选、点一下复制 id

网上那些对照表基本停在 221 号之前，之后 QQ 新增的一个都没有。QQ 再出新表情，重跑 `python tools/gen_face_table.py` 就更新 —— 用 NapCat 也行，脚本自带表情目录快照，不用装别的。

⚠️ 注意：

- 别填表里没有的 ID，比如 `2705`、`128536` 这类十六进制值。接口会返回 `ok`，但客户端贴出来是**空白**

## 排错

| 现象             | 处理                                                       |
| -------------- | -------------------------------------------------------- |
| 从没贴上过          | 看日志有没有 `贴表情失败`；401 多半是 token 格式不对                        |
| 贴出来是空白         | ID 填成了十六进制，换回 face id                                    |
| 日志刷 `已经设置过该表情` | 同一条消息被回了两次，属幂等成功，忽略即可                                    |
| 配置页样式没了        | `maibot_dashboard` 升级会覆盖 dist，重跑 `tools/inject_webui.py` |

## 兼容性

- MaiBot 1.0.0+ / maibot-plugin-sdk 2.5.1+ / Python 3.10+
- OneBot v11 HTTP，网关需要实现 `set_msg_emoji_like`（SnowLuma 已实测可用）
- Windows / Linux / macOS 一致，插件本体没有任何平台相关代码

## 致谢

感谢 [SnowLuma](https://snowluma.github.io/zh/) —— 它把 QQ 接成 OneBot v11 接口，本插件跑在上面，表情 ID 大全的数据也是用它缓存的目录整理的。

## 许可

MIT · 作者 **BabyQ** · 插件 ID `maibot_plugin-Response`
