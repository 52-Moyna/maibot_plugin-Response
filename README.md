# 回复标记插件(babyQ.maibot_reply-mark)

机器人回复某条消息时，顺手在被回复的那条消息上贴一个表情回应。

![插件配置界面（开启「美化配置页」后）](https://raw.githubusercontent.com/52-Moyna/maibot_plugin-Response/main/docs/config-panel.png?v=0.5.3)

`MaiBot 1.0.0+` · `maibot-plugin-sdk 2.5.1+` · 插件本体无需额外依赖 · Windows / Linux / macOS

> 🤖 **本插件完全由 AI（DeepSeek V4.1 Flash）开发。**  
> 踩到 bug、想加功能、或者觉得哪里写得不对，都欢迎去 [Issues](https://github.com/52-Moyna/maibot_plugin-Response/issues) 提。

---

## 功能

- **给被回复的消息贴表情** —— 默认 `[对的对的]`（`478`），原消息上直接挂一个表情回应
- **概率可调** —— 0~1 滑条，也能直接填数字，`0` 就是默认不贴
- **表情池随机抽** —— 可填多个 face id，一次贴 1~10 个，不重复
- **分群单独设** —— 每个群可以不一样：关掉某个群、改概率、换表情；没配的群走全局

## 安装

1. 整个目录放到 `<MaiBot>/plugins/` 下（目录名随意，插件身份看 `_manifest.json` 里的 `id`）
2. `cp config.example.toml config.toml`，填上网关地址和 Token
3. 重启 MaiBot，日志出现 `回复标记插件已加载` 就成功了

### 美化配置页（可选）

**默认关闭。** 打开后配置页变成分组卡片、滑条能直接填数字；关掉还原成 dashboard 默认样式。

![框架默认样式](https://raw.githubusercontent.com/52-Moyna/maibot_plugin-Response/main/docs/config-panel-default.png?v=0.5.3)

打开它：`config.toml` 里 `beautify_ui = true`，或者手动跑一次脚本。

```bash
python tools/inject_webui.py            # 注入
python tools/inject_webui.py --remove   # 移除
```

⚠️ **会改 dashboard 的文件，先看明白再开：** 只动 `<python-env>/Lib/site-packages/maibot_dashboard/dist/` —— 往 `index.html` 插一段带起止标记的引用，再复制两个文件进 `dist/assets/`。`--remove` 靠标记干净删掉、不碰别的；升级 dashboard 会覆盖，得重开一次。开关关着时**不会写入**；要是之前开过、后来关了（包括手改配置文件后重启），启动时会顺手把标记还原掉。

改完**必须重载整个 WebUI 页面**（`F5`），光刷新插件列表、切换插件都不算。

## 配置

WebUI 里是四张卡片，也可以直接改 `config.toml`。

| `[plugin]` 贴表情      | 默认     | 说明                       |
| ------------------- | ------ | ------------------------ |
| `enabled`           | `true` | 总开关                      |
| `react_probability` | `1.0`  | 贴表情概率，`0` = 默认不贴（分群可单独开） |
| `emoji_count`       | `1`    | 一次贴几个（1~10）              |
| `beautify_ui`       | `false` | 美化配置页，会改 dashboard 的 dist（见上），需重载 WebUI |

| `[emoji]` 表情池 | 默认        | 说明               |
| ------------- | --------- | ---------------- |
| `emoji_pool`  | `["478"]` | face id 列表，每次随机抽 |
| `emoji_id`    | `"478"`   | 池子为空时的兜底         |

| `[network]` 连接    | 默认                   | 说明                                              |
| ----------------- | -------------------- | ----------------------------------------------- |
| `host` / `port`   | `127.0.0.1` / `3000` | OneBot v11 HTTP 地址，NapCat 常见 `9999`             |
| `token`           | 空                    | 原样塞进 `Authorization` 头，SnowLuma 要写 `Bearer xxx` |
| `timeout_seconds` | `5`                  | 超时只记日志，不影响消息                                    |

### 分群设置

每个群可以单独设，互不打扰。**没写进列表的群一律走上面的全局配置**，所以只写想改的那几个群就行。

![分群设置](https://raw.githubusercontent.com/52-Moyna/maibot_plugin-Response/main/docs/group-rule.png?v=0.5.3)

| `[[group.rules]]`   | 默认              | 说明                                            |
| ------------------- | --------------- | --------------------------------------------- |
| `group_id`          | —               | QQ 群号                                         |
| `mode`              | `inherit（沿用全局）` | `inherit（沿用全局）` · `custom（自定义）` · `off（不贴表情）` |
| `react_probability` | `0.6`           | 本群概率，仅 `custom（自定义）` 生效                       |
| `emoji_count`       | `1`             | 本群一次贴几个，仅 `custom（自定义）` 生效                    |
| `emoji_pool`        | `[]`            | 本群表情池，留空则沿用全局，仅 `custom（自定义）` 生效              |

```toml
[[group.rules]]
group_id = "群号"
mode = "custom（自定义）"
react_probability = 0.5
emoji_count = 2
emoji_pool = ["478", "14"]

[[group.rules]]
group_id = "群号"
mode = "off（不贴表情）"
```

生效顺序：`off` → `custom` → `inherit` → 不在列表 → 全局。同一个群写了两条，以第一条为准。

`mode` 里的中文注释是给人看的，手写 TOML 时裸填 `"custom"`、`"off"` 也认。

#### 和全局的关系

`custom（自定义）` 是**独立**的：概率、个数、表情池全看本群那份，不跟全局相乘，也不被全局压住。

| 全局概率 = `0` 时           | 结果        |
| ---------------------- | --------- |
| 分群 `custom`，概率 0.9     | 贴         |
| 分群 `custom`，概率 1、表情池留空 | 贴（用全局表情池） |
| 分群 `inherit`           | 不贴（继承了 0） |
| 分群 `off`               | 不贴        |
| 没配分群的群                 | 不贴        |

所以「平时不怎么贴、只给几个群贴」就直接这么配：全局概率拉到 `0`，要贴的群加一条规则，`mode` 选 `custom（自定义）`、概率填 `1`。

⚠️ 两个坑：

- 全局 `0` 的时候，选 `inherit` 的群**等于白写** —— 想贴就得选 `custom` 并显式给概率
- 「启用插件」总开关关掉后，分群**一律不生效**，那个是插件级开关，分群管不了

## 表情 ID

填 QQ 表情的 **face id**，不是 Unicode 码点。

- 完整 **299 个（含 190 个新版）** → [qq-face-ids.md](https://github.com/52-Moyna/maibot_plugin-Response/blob/main/docs/qq-face-ids.md)，`Ctrl+F` 搜名字
- 想更顺手 → [打开速查器](https://52-moyna.github.io/maibot_plugin-Response/face-lookup.html)，搜索、筛选、点一下复制 id

网上那些对照表基本停在 221 号之前，之后 QQ 新增的一个都没有。QQ 再出新表情，重跑 `python tools/gen_face_table.py` 就更新 —— 用 NapCat 也行，脚本自带表情目录快照，不用装别的。

想知道比上次多了哪些：`python tools/gen_face_table.py --diff --check`。

⚠️ 注意：

- 别填表里没有的 ID，比如 `2705`、`128536` 这类十六进制值。接口会返回 `ok`，但客户端贴出来是**空白**
- 只有「重抓表情图片」才需要 Pillow（`pip install Pillow`），`tools/fetch_face_images.py` 用；生成 ID 表和日常贴表情都不需要

## 排错

| 现象             | 处理                                                                              |
| -------------- | ------------------------------------------------------------------------------- |
| 私聊里没反应         | 正常。QQ 不支持给私聊消息贴表情，插件直接跳过、不报错，只在卸载日志里记一笔                                         |
| 某个群不贴          | 查分群规则里这个群是不是 `off`；或者全局概率是 `0` 而它选了 `inherit`                                   |
| 从没贴上过          | 看日志有没有 `贴表情失败`；401 多半是 token 格式不对                                               |
| 贴出来是空白         | ID 填成了十六进制，换回 face id                                                           |
| 日志刷 `已经设置过该表情` | 同一条消息被回了两次，属幂等成功，忽略即可                                                           |
| 配置页样式没了        | 重跑 `python tools/inject_webui.py`；dashboard 升级会覆盖掉，改完记得 `F5` 重载 |

## 兼容性

- MaiBot 1.0.0+ / maibot-plugin-sdk 2.5.1+ / Python 3.10+
- OneBot v11 HTTP，网关需要实现 `set_msg_emoji_like`（SnowLuma 已实测可用）
- Windows / Linux / macOS 一致，插件本体没有任何平台相关代码

## 致谢

感谢 [MaiBot](https://github.com/Mai-with-u/MaiBot) —— 它提供插件框架，本插件跑在它上面。

感谢 [SnowLuma](https://snowluma.github.io/zh/) —— 它把 QQ 接成 OneBot v11 接口，贴表情走的就是这条链路，表情 ID 大全的数据也是用它缓存的目录整理的。

## 许可

MIT · 作者 **BabyQ** · 插件 ID `babyQ.maibot_reply-mark`
