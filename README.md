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

配置按用途分成三段，WebUI 里就是三张卡片：

```toml
# 贴表情
[plugin]
enabled = true
config_version = "0.4.0"     # 内部字段，WebUI 里隐藏
react_probability = 1.0      # 0~1，滑条
emoji_count = 1              # 1~10，滑条

# 表情池
[emoji]
emoji_pool = ["478"]         # 可多个，随机抽
emoji_id = "478"             # 池为空时兜底

# 连接
[network]
host = "127.0.0.1"
port = 3000                  # SnowLuma 网关；NapCat 常见 9999
token = "Bearer xxxx"        # SnowLuma 要求连 Bearer 前缀一起写
timeout_seconds = 5
```

> v0.4.0 起从单段 `[plugin]` 拆成 `[plugin]` / `[emoji]` / `[network]` 三段。旧配置文件需要手动迁到新结构（值不变，只是换了段名）。

## 界面上做了什么

框架的插件配置页是「一张卡片堆字段」，纯靠 schema 元数据 + 一层作用域 CSS 改观感，**没有 patch 前端代码**：

**字段层（插件自己能控制的）**

| 手段 | 效果 |
|---|---|
| 三段配置 | WebUI 渲染成三张带标题和说明的卡片，不再是一整坨 |
| `hidden: True` | `config_version` 不再是用户可见字段 |
| `hint` | 每个控件下面一行小字说明（前端 `p.text-muted-foreground`） |
| `x-widget: "slider"` | 概率、数量渲染成滑条（前端按 `ui_type` 分支，SDK 读 `x-widget`） |
| `order` | 字段排序 |

**样式层（`mb-plugin-config.css`，只在 `/plugin-config` 生效）**

- 卡片：14px 圆角 + 双层阴影，卡片头加左侧主色竖条与淡渐变底
- 主开关字段（带 switch 的那个）拉成通栏色块，视觉上有落脚点
- 滑条：轨道收细到 6px，已选区间主色渐变，滑块带外发光；数值做成胶囊徽标
- 滑条行限宽 560px，不再横贯整卡
- 输入框：内嵌底色 + 聚焦主色描边；列表项的「添加项目」改成虚线主色按钮

作用域靠 `mb-plugin-config.js` 在 `/plugin-config` 路由给 `<html>` 打 `data-mb-plugin-config="1"`，离开就摘掉 —— 所以「麦麦设置」等页面完全不受影响。全部颜色走 `hsl(var(--primary)/.12)` 这类主题变量，明暗主题自动适配。

框架不给改的部分：整页布局、组件结构、字段控件种类都由 dashboard 决定，插件只能影响 schema 元数据与样式，不能替换页面本体。

## 数值可以手动填

框架把滑条右边的数值渲染成只读 `<span>`，改文本不会写回配置。`mb-plugin-config.js` 把它换成真输入框：**输入数字 → 回车或失焦生效，滑条同步移动**。

实现要点：

- **输入 → 滑条**：不是直接改 DOM，而是给滑条 thumb 派发键盘事件（`Home` / `PageUp` / `ArrowRight` / `ArrowLeft`），驱动框架自己的 onChange ⇒ 滑条位置和配置值一起变，保存才有效。
- **滑条 → 输入框**：`MutationObserver` 监听 thumb 的 `aria-valuenow`，拖完自动回填。
- 超范围自动夹到 `min`/`max`；小数按步长量化；连续输入排队执行，取最后一次。
- 🔴 **踩过的坑**：Radix 的 `aria-valuenow` 是**异步**更新的。按完键立刻读拿到的是旧值，第一版就是因此"填 4 变成 2"。必须每次按键后等一帧（`requestAnimationFrame` + `setTimeout`）再读；末尾再加一段收敛校验兜浮点误差。

## 跨平台（Windows / Linux / macOS）

**插件本体零平台依赖**：只用了 `http.client`、`asyncio`、`json`、`logging`，没有任何路径硬编码、注册表或 shell 调用，Linux 上行为完全一致。

**界面美化是注入到 dashboard 包里的**，需要在目标机器上执行一次：

```bash
python tools/inject_webui.py            # 注入 / 更新
python tools/inject_webui.py --check    # 只看状态
python tools/inject_webui.py --remove   # 卸载（保留资源文件）
```

- 必须在 **MaiBot 实际运行所用的 Python 环境**里跑。脚本靠 `import maibot_dashboard` 定位 `dist` 目录，不写死路径，所以 Windows 一键包、Linux venv、系统 Python 都能用。
- 幂等：重复执行只刷新 `?v=` 时间戳（防止浏览器强缓存旧文件），不会重复插入标签。
- Linux 如果 dashboard 装在系统目录（如 `/usr/lib/python3/dist-packages`），写入可能需要 `sudo`，或改用 venv 安装。
- 🔴 **升级 `maibot_dashboard` 包会替换整个 dist，注入内容会丢**，升级后重跑一次即可。
- 资源正本在本仓库 `webui/` 下，页面效果只作用于 `/plugin-config`，不影响其它页面。
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

**第三个坑**：同一条消息被回第二次、贴同一个表情时，QQ 报 `OIDB error 65002 ... 已经设置过该表情`。这是幂等成功（表情本来就在），插件已按成功处理，不刷警告。

**还有**：填 `2705`、`128536` 这类 Unicode 十六进制字面值不行，会贴出空白。系统 emoji 要用码点的十进制值（✅ 是 `9989`），但表情包/系统表情用的是 face id，两套编号不通用。

## 依赖

- MaiBot 1.0.0+，maibot-plugin-sdk 2.5.1+
- 一个支持 `set_msg_emoji_like` 的 OneBot v11 HTTP 服务（SnowLuma / NapCat）

不需要任何额外 Python 包。

## 已知情况

- 与「麦麦贴表情」（`davidblackcn.maibot-message-react-plugin`）不冲突，但两者都会贴表情：那个是按概率主动贴别人的消息，本插件是确定性地贴「被回复的那条」。同群同时开会出现两套表情回应。
- 插件 ID 仍是 `workbuddy.reply_confirm`（改名会丢插件状态与已生成的配置），只有显示名是「回复标记插件」。
- v0.1.0/0.2.0 的「用表情替代引用框」（`replace_quote`）已在 0.3.0 移除：引用框不再被摘掉。
