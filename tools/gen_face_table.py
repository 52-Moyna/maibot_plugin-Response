#!/usr/bin/env python3
"""生成 QQ 系统表情 ID 大全（Markdown 速查表 + 单页可搜索速查器）。

为什么不用网上的对照表
--------------------
网上流传的 face id 表（go-cqhttp / 各种博客）基本都停在 2019 年那批老编号，
221 之后 QQ 新增的表情一个都没有。而 SnowLuma 网关会把 QQ 服务端下发的系统
表情目录缓存成 ``data/sys-face-catalog.json``，里面每条都带：

- ``qSid``   —— 就是这个表要的 **face id**（``set_msg_emoji_like`` 填的值）
- ``qDes``   —— 中文名，形如 ``/对的对的``
- ``emCode`` —— QQ 内部另一套 em 编号，跟 face id 不是一回事
- ``url``    —— 表情资源 zip（本项目不使用，避免搬运版权素材）

所以直接读这个文件生成，新版表情天然包含在内。

用 NapCat 怎么办
--------------
不用管。这份目录是 **QQ 服务端下发的，跟用哪个网关无关** —— SnowLuma 只是顺手
把它缓存成了 json（它走的是 QQ 内部的 OIDB 协议，需要登录态，普通 HTTP 复现不了）。

仓库里带了一份裁剪过的快照 ``tools/face_catalog_snapshot.json``（只有 id / 名字 /
em 编号，约 20KB），所以：

- 有 SnowLuma：自动读它的实时文件，永远最新
- 只有 NapCat：自动退回内置快照，直接就能生成，不需要装任何东西
- 两边都有：按 ``fetchedAt`` 取新的那个

有 SnowLuma 的人想在 QQ 出新表情后刷新快照，跑 ``--update-snapshot`` 再提交。

用法
----
    python tools/gen_face_table.py                       # 自动选数据源（实时 > 快照）
    python tools/gen_face_table.py --catalog D:/x.json   # 手动指定目录文件
    python tools/gen_face_table.py --check               # 只看统计，不写文件
    python tools/gen_face_table.py --diff --check        # 只看「比内置快照多了/少了什么」
    python tools/gen_face_table.py --update-snapshot     # 用实时数据刷新内置快照

产出（默认写到插件 docs/ 下）
--------------------------
    docs/qq-face-ids.md     分类速查表，可直接 Ctrl+F
    docs/face-lookup.html   单页速查器，搜索 / 筛选 / 一键复制 id
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

#: 网上旧对照表普遍只到 221 左右，之后的编号都是后来新增的。
LEGACY_MAX_ID = 221

#: 包展示顺序与说明。没列到的包走默认说明。
PACK_META: dict[str, tuple[str, str]] = {
    "小黄脸表情": ("小黄脸", "经典那批黄脸，日常最常用"),
    "QQ黄脸": ("QQ 黄脸", "后来补的黄脸与节庆款"),
    "超级表情": ("超级表情", "带出场动画的大表情"),
    "汪汪": ("汪汪", "主题表情包"),
    "喜花妮": ("喜花妮", "主题表情包"),
    "企鹅": ("企鹅", "主题表情包"),
    "噗噗星人": ("噗噗星人", "主题表情包"),
    "隐藏表情": ("隐藏 / 活动", "节日活动款，可能随活动下线"),
    "MagicFace": ("MagicFace", "特殊交互表情"),
    "emoji 表情": ("emoji（Unicode）", "这类填的是 Unicode 码点，不是 face id，见文末说明"),
}

#: 命中这些关键词的包会被归到「限时表情」。
LIMITED_KEYWORDS = ("限时",)


#: 内置快照：给没有 SnowLuma 的用户（NapCat / Lagrange / LLOneBot…）兜底用。
SNAPSHOT_PATH = Path(__file__).resolve().parent / "face_catalog_snapshot.json"

#: 表情雪碧图（由 fetch_face_images.py 抓取生成）。没有就生成不带图的版本。
SPRITE_PATH = Path(__file__).resolve().parent / "face_sprite.json"


def load_sprite() -> dict[str, Any] | None:
    """读雪碧图数据；缺失或损坏就当没有，退回纯文字版。"""

    if not SPRITE_PATH.is_file():
        return None
    try:
        data = json.loads(SPRITE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("image") or not data.get("index"):
        return None
    return data


def _peek_fetched(path: Path) -> int:
    """取目录文件的抓取时间戳（毫秒）。没有 fetchedAt 就退回文件修改时间。"""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = raw.get("fetchedAt")
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    except (OSError, ValueError):
        pass
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def resolve_catalog(explicit: str | None) -> tuple[Path, bool]:
    """挑数据源：手动指定 > 实时目录 > 内置快照，同类型取抓取时间最新的。

    返回 (路径, 是否用的内置快照)。
    """

    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"找不到指定的目录文件：{path}")
        return path, path == SNAPSHOT_PATH

    here = Path(__file__).resolve()
    # 插件装在哪不确定，层级可能不够，越界就退回脚本所在目录再往上两级
    parents = here.parents
    project_root = parents[4] if len(parents) > 4 else here.parent
    live_candidates = [
        # plugins/<插件>/tools/ -> 项目根 -> 同级 SnowLuma
        project_root / "SnowLuma" / "data" / "sys-face-catalog.json",
        Path.cwd() / "SnowLuma" / "data" / "sys-face-catalog.json",
        Path.cwd() / "data" / "sys-face-catalog.json",
        here.parent / "sys-face-catalog.json",
        Path.home() / "SnowLuma" / "data" / "sys-face-catalog.json",
        Path("/opt/SnowLuma/data/sys-face-catalog.json"),
    ]

    found: list[tuple[Path, bool]] = [(p, False) for p in live_candidates if p.is_file()]
    if SNAPSHOT_PATH.is_file():
        found.append((SNAPSHOT_PATH, True))

    if not found:
        raise SystemExit(
            "没找到任何表情目录数据源。\n"
            "内置快照 tools/face_catalog_snapshot.json 缺失或被删了，\n"
            "请重新从仓库获取，或用 --catalog 手动指定 sys-face-catalog.json。"
        )

    # 抓取时间最新的胜出；时间相同时优先实时文件。
    return max(found, key=lambda item: (_peek_fetched(item[0]), not item[1]))


def load_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读目录文件，拍平成一行一个表情。"""

    raw = json.loads(path.read_text(encoding="utf-8"))
    packs = raw.get("packs") or []
    if not packs:
        raise SystemExit("目录文件里没有 packs，格式不对。")

    rows: list[dict[str, Any]] = []
    for pack in packs:
        pack_name = str(pack.get("packName") or "未命名").strip()
        for item in pack.get("emojis") or []:
            sid = str(item.get("qSid") or "").strip()
            if not sid:
                continue
            name = str(item.get("qDes") or "").strip().lstrip("/")
            is_face = sid.isdigit()
            rows.append(
                {
                    "sid": sid,
                    # emoji 类填的是 Unicode 码点，直接给十进制
                    "fill": sid if is_face else str(ord(sid[0])),
                    "numeric": is_face,
                    "kind": "face" if is_face else "emoji",
                    "id": int(sid) if is_face else ord(sid[0]),
                    "name": name or sid,
                    "em": str(item.get("emCode") or "").strip(),
                    "pack": pack_name,
                    "new": is_face and int(sid) > LEGACY_MAX_ID,
                }
            )

    # 同一个 id 可能同时挂在「小黄脸」和「超级表情」下，名字一致，按先出现的保留
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row["sid"] in seen:
            continue
        seen.add(row["sid"])
        deduped.append(row)
    rows = deduped

    fetched_ms = raw.get("fetchedAt")
    meta = {
        "fetched_at": (
            datetime.fromtimestamp(fetched_ms / 1000).strftime("%Y-%m-%d %H:%M")
            if isinstance(fetched_ms, (int, float))
            else "未知"
        ),
        "packs": [str(p.get("packName") or "").strip() for p in packs],
        "source": str(path),
    }
    return rows, meta


def _pack_label(pack_name: str) -> str:
    """包名 -> 展示名。限时包统一归到「限时」。"""

    if any(word in pack_name for word in LIMITED_KEYWORDS):
        return "限时表情"
    return PACK_META.get(pack_name, (pack_name, "其他系统表情"))[0]


def _pack_desc(pack_name: str) -> str:
    if any(word in pack_name for word in LIMITED_KEYWORDS):
        return "活动限时款，过期会失效"
    return PACK_META.get(pack_name, (pack_name, "其他系统表情"))[1]


def _ordered_packs(rows: list[dict[str, Any]]) -> list[str]:
    """按「常用优先」排包，其余保持原始顺序。"""

    seen: list[str] = []
    for row in rows:
        if row["pack"] not in seen:
            seen.append(row["pack"])

    priority = ["小黄脸表情", "QQ黄脸", "超级表情", "隐藏表情", "MagicFace"]
    head = [p for p in priority if p in seen]
    tail = [p for p in seen if p not in head and not any(w in p for w in LIMITED_KEYWORDS)]
    limited = [p for p in seen if any(w in p for w in LIMITED_KEYWORDS)]
    emoji_packs = [p for p in tail if p == "emoji 表情"]
    tail = [p for p in tail if p != "emoji 表情"]
    return head + tail + limited + emoji_packs


def write_snapshot(src: Path, dst: Path = SNAPSHOT_PATH) -> tuple[int, int]:
    """把实时目录裁成精简快照（只留 qSid / qDes / emCode），供无 SnowLuma 的用户用。"""

    if src.resolve() == dst.resolve():
        raise SystemExit("当前数据源就是内置快照，没有实时数据可刷新。")

    raw = json.loads(src.read_text(encoding="utf-8"))
    packs = raw.get("packs") or []
    slim = {
        "schemaVersion": raw.get("schemaVersion", 1),
        "fetchedAt": raw.get("fetchedAt"),
        "note": "裁剪版：只保留 qSid / qDes / emCode，供没有 SnowLuma 的用户生成表情表。",
        "packs": [
            {
                "packName": pack.get("packName"),
                "emojis": [
                    {
                        "qSid": item.get("qSid"),
                        "qDes": item.get("qDes"),
                        "emCode": item.get("emCode"),
                    }
                    for item in (pack.get("emojis") or [])
                ],
            }
            for pack in packs
        ],
    }
    dst.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(slim["packs"]), sum(len(p["emojis"]) for p in slim["packs"])


def render_markdown(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    face_rows = [r for r in rows if r["kind"] == "face"]
    emoji_rows = [r for r in rows if r["kind"] == "emoji"]
    uniq_ids = {r["id"] for r in face_rows}
    new_count = len({r["id"] for r in face_rows if r["new"]})

    out: list[str] = []
    out.append("# QQ 表情 ID 大全（face id）")
    out.append("")
    out.append(
        f"> 数据源：{meta.get('source_md', 'QQ 系统表情目录')}（抓取于 {meta['fetched_at']}），"
        f"共 **{len(uniq_ids)} 个 face id**，另有 {len(emoji_rows)} 个 emoji 码点。"
    )
    out.append(">")
    out.append(
        f"> 其中 **{new_count} 个**是编号大于 {LEGACY_MAX_ID} 的新版表情 —— "
        "网上流传的旧对照表基本都缺这一段，这份表是完整的。"
    )
    out.append(">")
    out.append(
        "> 编号会跳号（0~524 之间缺一部分），那是 QQ 已经下线的老表情，不是表漏了。"
    )
    out.append(">")
    out.append(
        "> **两种编号**：系统表情填 face id（如 `478`）；Unicode emoji 填**十进制码点**"
        "（如 🐛 = `128027`、😧 = `128551`）。下面列的 68 个 emoji 只是 QQ 表情面板里那套，"
        "**其他 emoji 只要知道码点也照样能贴**，不必在表里。"
    )
    out.append("")
    out.append("填在插件 `[emoji] emoji_pool` 里的就是下表的 **id** 列（十进制字符串）。")
    out.append("")
    out.append("| 想做什么 | 怎么做 |")
    out.append("| --- | --- |")
    out.append("| 按名字找 id | 本页 `Ctrl+F` 搜中文名，或打开同目录的 `face-lookup.html` |")
    out.append("| 按 id 找名字 | 搜数字，或看下面「按编号排序」一节 |")
    out.append("| 验证真的贴上了 | 见下面「实测」一节的 `get_emoji_likes` 用法 |")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 实测：表里的 id 真能贴上")
    out.append("")
    out.append("2026-10-25 在测试群一条消息上按类别各取一个，先 `set_msg_emoji_like`，再用")
    out.append("`get_emoji_likes` 逐个回查（`data.emoji_like_list` 非空才算真贴上）：")
    out.append("")
    out.append("| id | 类别 | set 返回 | 回查 |")
    out.append("| --- | --- | --- | --- |")
    out.append("| `478` | 小黄脸 / 超级表情 | ok | ✅ 贴上 |")
    out.append("| `500` | QQ 黄脸（秋秋赏月） | ok | ✅ 贴上 |")
    out.append("| `512` | 限时表情（王者召唤） | ok | ✅ 贴上 |")
    out.append("| `320` | 超级表情（庆祝） | ok | ✅ 贴上 |")
    out.append("| `358` | MagicFace | ok | ✅ 贴上 |")
    out.append("| `9989` | emoji 码点（✅） | ok | ✅ 贴上 |")
    out.append("| `999999` | 不存在的 id | **ok** | ❌ 空 |")
    out.append("")
    out.append("🔴 最后一行是重点：**`set_msg_emoji_like` 对不存在的 id 也返回 `ok`**，")
    out.append("光看返回值判断不了成败。回查要这样调：")
    out.append("")
    out.append("```json")
    out.append('POST /get_emoji_likes   {"message_id": "<消息id>", "emoji_id": "478"}')
    out.append("-> data.emoji_like_list 有内容 = 真贴上；空数组 = 没贴上")
    out.append("```")
    out.append("")
    out.append("---")
    out.append("")

    for pack_name in _ordered_packs(rows):
        items = [r for r in rows if r["pack"] == pack_name]
        if not items:
            continue
        # 数字 id 按大小排，emoji 类的保持原顺序
        if all(r["numeric"] for r in items):
            items.sort(key=lambda r: r["id"])
        out.append(f"## {_pack_label(pack_name)}（{len(items)} 个）")
        out.append("")
        out.append(f"*{_pack_desc(pack_name)}*")
        out.append("")
        out.append("| id | 名称 | em 编号 | 备注 |")
        out.append("| --- | --- | --- | --- |")
        for r in items:
            if r["numeric"]:
                note = "新版" if r["new"] else ""
            else:
                note = "emoji 码点"
            out.append(f"| `{r['fill']}` | {r['name']} | {r['em'] or '—'} | {note} |")
        out.append("")

    out.append("---")
    out.append("")
    out.append("## 按编号排序（查 id 用这张）")
    out.append("")
    out.append("| id | 名称 | 分类 |")
    out.append("| --- | --- | --- |")
    for r in sorted(face_rows, key=lambda r: r["id"]):
        mark = " · 新版" if r["new"] else ""
        out.append(f"| `{r['id']}` | {r['name']} | {_pack_label(r['pack'])}{mark} |")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 三套编号别混")
    out.append("")
    out.append("| 类型 | 填什么 | 例子 |")
    out.append("| --- | --- | --- |")
    out.append("| QQ 系统表情 / 表情包 | **face id（十进制）** | `478` = 对的对的 |")
    out.append("| QQ 的 emoji 表情 | **Unicode 码点（十进制）** | `9989` = ✅ |")
    out.append("| em 编号（`emCode`） | 只在本表里做对照，**不能填** | `100` 对应的 face id 是 `14` |")
    out.append("")
    out.append("🔴 坑：填十六进制字面值（`2705`、`128536`）接口会返回 `ok`，但客户端贴出来是空白。")
    out.append("")
    out.append("## 这份表怎么更新")
    out.append("")
    out.append("QQ 出新表情后重跑一次即可：")
    out.append("")
    out.append("```bash")
    out.append("python tools/gen_face_table.py")
    out.append("```")
    out.append("")
    out.append("脚本自己选数据源，**不用 SnowLuma 也能用**：")
    out.append("")
    out.append("| 你的环境 | 读哪个文件 | 备注 |")
    out.append("| --- | --- | --- |")
    out.append("| SnowLuma | 它的 `<SnowLuma>/data/sys-face-catalog.json` | 实时，永远最新 |")
    out.append("| NapCat / Lagrange / LLOneBot | 插件自带 `tools/face_catalog_snapshot.json` | 开箱即用，不装 SnowLuma 也行 |")
    out.append("| 两边都有 | 抓取时间更新的那个 | — |")
    out.append("")
    out.append("- `--update-snapshot` 用实时目录刷新内置快照（需要有 SnowLuma 的环境）")
    out.append("- `--catalog <path>` 手动指定目录文件；`--check` 只看统计不写文件")
    out.append("")
    out.append("> 🔴 表里的 id 是 **QQ 服务端下发的编号，跟用哪个网关无关**。但能不能贴出来，取决于")
    out.append("> 网关的 `set_msg_emoji_like` 实现 —— 换网关后建议先用 `get_emoji_likes` 抽查几个，再批量填进配置。")
    out.append("")
    return "\n".join(out)


HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 表情 ID 速查器</title>
<style>
  :root {
    --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --line: #e5e7eb;
    --card: #fafafa; --accent: #ea580c; --accent-soft: rgba(234, 88, 12, .1);
    --new: #16a34a; --new-soft: rgba(22, 163, 74, .12);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16181c; --fg: #e8e8ea; --muted: #9aa0a6; --line: #2c2f36;
      --card: #1e2126; --accent: #fb923c; --accent-soft: rgba(251, 146, 60, .14);
      --new: #4ade80; --new-soft: rgba(74, 222, 128, .14);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 28px 20px 60px; background: var(--bg); color: var(--fg);
    font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 6px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .sub b { color: var(--accent); font-weight: 600; }
  .bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  #q {
    flex: 1 1 260px; min-width: 200px; padding: 10px 14px; font-size: 14px;
    border: 1px solid var(--line); border-radius: 10px; background: var(--card); color: var(--fg);
    outline: none;
  }
  #q:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  #conv {
    flex: 0 1 330px; min-width: 220px; padding: 10px 14px; font-size: 14px;
    border: 1px solid var(--line); border-radius: 10px; background: var(--card); color: var(--fg);
    outline: none;
  }
  #conv:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  #convOut { font-size: 13px; color: var(--muted); white-space: nowrap; }
  #convOut b { color: var(--accent); font-size: 17px; margin: 0 4px; cursor: pointer; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
  .chip {
    padding: 5px 12px; border-radius: 999px; border: 1px solid var(--line);
    background: var(--card); color: var(--muted); cursor: pointer; font-size: 13px;
    user-select: none;
  }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
  .chip.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 10px; }
  .item {
    position: relative; padding: 12px 10px 10px; border: 1px solid var(--line);
    border-radius: 12px; background: var(--card); cursor: pointer; text-align: center;
    transition: transform .08s ease, border-color .12s ease;
  }
  .item:hover { border-color: var(--accent); transform: translateY(-1px); }
  .item:active { transform: scale(.97); }
  .face {
    display: block; width: var(--face-size, 40px); height: var(--face-size, 40px);
    margin: 0 auto 5px; background-image: var(--sprite); background-repeat: no-repeat;
    background-size: var(--sprite-size, auto); visibility: hidden;
    image-rendering: -webkit-optimize-contrast;
  }
  .face.on { visibility: visible; }
  .item .id { font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: .5px; }
  .item .name { font-size: 12px; color: var(--muted); margin-top: 3px; word-break: break-all; }
  .item .badge {
    position: absolute; top: 5px; right: 5px; font-size: 10px; padding: 1px 5px;
    border-radius: 6px; background: var(--new-soft); color: var(--new); font-weight: 600;
  }
  .item .badge.code { background: var(--accent-soft); color: var(--accent); }
  .chip.new-chip { flex: 0 0 auto; padding: 8px 14px; }
  .chip.new-chip.on { background: var(--new-soft); border-color: var(--new); color: var(--new); }
  .item.copied { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .empty { color: var(--muted); padding: 30px 0; text-align: center; }
  #toast {
    position: fixed; left: 50%; bottom: 28px; transform: translate(-50%, 20px);
    background: var(--accent); color: #fff; padding: 9px 18px; border-radius: 999px;
    font-size: 13px; opacity: 0; pointer-events: none; transition: all .18s ease;
  }
  #toast.on { opacity: 1; transform: translate(-50%, 0); }
  footer { margin-top: 34px; color: var(--muted); font-size: 12px; line-height: 1.9; }
  footer code { background: var(--card); border: 1px solid var(--line); padding: 1px 5px; border-radius: 5px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>QQ 表情 ID 速查器</h1>
  <div class="sub">
    共 <b>__TOTAL__</b> 个 face id__PIC_NOTE__，其中 <b>__NEW__</b> 个是编号 221 之后的新版表情；
    另有 <b>__EMOJI__</b> 个 emoji 码点（角标「码」）。数据抓取于 __FETCHED__。点卡片即复制。
  </div>

  <div class="bar">
    <input id="q" type="search" placeholder="搜 id / 中文名 / em 编号，例如 478、对的、10500">
    <input id="conv" type="text" placeholder="粘贴任意 emoji 换算码点，如 🐛">
    <span id="convOut"></span>
  </div>
  <div class="chips" id="chips"></div>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" hidden>没有匹配的表情</div>

  <footer>
    填进插件 <code>[emoji] emoji_pool</code> 的是卡片上的 <b>数字 id</b>。<br>
    系统表情填 face id（<code>478</code>）；Unicode emoji 填<b>十进制码点</b>（🐛 = <code>128027</code>）——
    上面 68 个只是 QQ 表情面板那套，别的 emoji 用右上角输入框换算后照样能贴。<br>
    🔴 不要填十六进制字面值（<code>2705</code>、<code>128536</code>）：接口返回 ok，但客户端贴出来是空白。<br>
    验证是否真贴上，用 <code>get_emoji_likes</code> 查那条消息 —— <code>set_msg_emoji_like</code> 对无效 id 也会返回 ok。
  </footer>
</div>
<div id="toast"></div>

<script>
const DATA = __DATA__;
const SPRITE = __SPRITE__;
const NEW_MIN = __NEW_MIN__;

const grid = document.getElementById('grid');
const chips = document.getElementById('chips');
const q = document.getElementById('q');
const empty = document.getElementById('empty');
const toast = document.getElementById('toast');

// emoji → 十进制码点。不在下表里的 emoji 也能贴，只要有码点。
function firstCodepoint(text) {
  for (const ch of String(text || '').trim()) {
    const cp = ch.codePointAt(0);
    if (cp === 0xfe0f || cp === 0xfe0e || /\\s/.test(ch)) continue;
    return cp;
  }
  return 0;
}
const conv = document.getElementById('conv');
const convOut = document.getElementById('convOut');
if (conv && convOut) {
  conv.addEventListener('input', () => {
    const cp = firstCodepoint(conv.value);
    if (!cp) { convOut.textContent = ''; return; }
    convOut.innerHTML = '→ 十进制 <b title="点一下复制">' + cp + '</b>';
    convOut.querySelector('b').onclick = () => copyPlain(String(cp), '已复制 emoji 码点 ' + cp);
  });
}

// 雪碧图：整张图设一次 CSS 变量，每张卡片只算 background-position
let spriteRows = 0;
if (SPRITE) {
  spriteRows = Math.floor(Math.max(...Object.values(SPRITE.index)) / SPRITE.cols) + 1;
  const root = document.documentElement.style;
  root.setProperty('--sprite', 'url("' + SPRITE.image + '")');
  root.setProperty('--face-size', SPRITE.size + 'px');
  root.setProperty('--sprite-size', SPRITE.cols * SPRITE.size + 'px ' + spriteRows * SPRITE.size + 'px');
}

function faceStyle(fill) {
  if (!SPRITE) return '';
  const pos = SPRITE.index[fill];
  if (pos === undefined) return '';
  const x = -(pos % SPRITE.cols) * SPRITE.size;
  const y = -Math.floor(pos / SPRITE.cols) * SPRITE.size;
  return ' style="background-position:' + x + 'px ' + y + 'px"';
}

let pack = '全部';
let onlyNew = false;

// 分类 chips
const packs = ['全部', ...new Set(DATA.map((d) => d.p))];
packs.forEach((name, i) => {
  const el = document.createElement('div');
  el.className = 'chip' + (i === 0 ? ' on' : '');
  el.dataset.role = 'pack';
  el.textContent = name;
  el.onclick = () => {
    pack = name;
    [...chips.querySelectorAll('[data-role="pack"]')].forEach((c) => c.classList.remove('on'));
    el.classList.add('on');
    render();
  };
  chips.appendChild(el);
});

// 独立开关：只看编号 221 之后的新版，放搜索框同一行
const newChip = document.createElement('div');
newChip.className = 'chip new-chip';
newChip.textContent = '只看新版';
newChip.onclick = () => {
  onlyNew = !onlyNew;
  newChip.classList.toggle('on', onlyNew);
  render();
};
document.querySelector('.bar').appendChild(newChip);

function match(d, key) {
  if (!key) return true;
  return (
    d.s.includes(key) ||
    d.n.toLowerCase().includes(key) ||
    (d.e && d.e.includes(key))
  );
}

function render() {
  const key = q.value.trim().toLowerCase();
  const list = DATA.filter((d) => {
    if (pack !== '全部' && d.p !== pack) return false;
    if (onlyNew && !(d.k === 'face' && d.i > NEW_MIN)) return false;
    return match(d, key);
  });
  grid.innerHTML = '';
  empty.hidden = list.length > 0;

  const frag = document.createDocumentFragment();
  list.forEach((d) => {
    const el = document.createElement('div');
    el.className = 'item';
    const isNew = d.k === 'face' && d.i > NEW_MIN;
    const badge = isNew
      ? '<span class="badge">新</span>'
      : d.k === 'emoji'
        ? '<span class="badge code">码</span>'
        : '';
    const hasPic = SPRITE && SPRITE.index[d.s] !== undefined;
    const face = SPRITE
      ? '<div class="face' + (hasPic ? ' on' : '') + '"' + faceStyle(d.s) + '></div>'
      : '';
    el.innerHTML =
      badge + face + '<div class="id">' + d.i + '</div>' + '<div class="name">' + d.n + '</div>';
    el.title = d.p + (d.e ? ' · em ' + d.e : '');
    el.onclick = () => copy(d, el);
    frag.appendChild(el);
  });
  grid.appendChild(frag);
}

function copyPlain(text, msg) {
  const done = () => {
    toast.textContent = msg;
    toast.classList.add('on');
    clearTimeout(copyPlain._t);
    copyPlain._t = setTimeout(() => toast.classList.remove('on'), 1300);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallback(text, done));
  } else {
    fallback(text, done);
  }
}

function copy(d, el) {
  const text = d.s;
  const done = () => {
    el.classList.add('copied');
    setTimeout(() => el.classList.remove('copied'), 600);
    toast.textContent = '已复制 ' + text + '（' + d.n + '）';
    toast.classList.add('on');
    clearTimeout(copy._t);
    copy._t = setTimeout(() => toast.classList.remove('on'), 1300);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallback(text, done));
  } else {
    fallback(text, done);
  }
}

function fallback(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) { /* 忽略 */ }
  document.body.removeChild(ta);
  done();
}

q.addEventListener('input', render);
render();
</script>
</body>
</html>
"""


def render_html(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    face_rows = [r for r in rows if r["kind"] == "face"]
    emoji_rows = [r for r in rows if r["kind"] == "emoji"]
    uniq = sorted({r["id"] for r in face_rows})
    new_count = len([i for i in uniq if i > LEGACY_MAX_ID])

    # face id 按编号升序在前，emoji 码点在后
    ordered = sorted(face_rows, key=lambda r: r["id"]) + sorted(emoji_rows, key=lambda r: r["id"])

    # 前端只要短键，减小体积
    payload = [
        {
            "s": r["fill"],
            "i": r["id"],
            "n": r["name"],
            "e": r["em"],
            "p": _pack_label(r["pack"]),
            "k": r["kind"],
        }
        for r in ordered
    ]

    sprite = load_sprite()
    sprite_payload = (
        {
            "size": sprite["size"],
            "cols": sprite["cols"],
            "image": sprite["image"],
            "index": {r["fill"]: sprite["index"][r["fill"]] for r in ordered if r["fill"] in sprite["index"]},
        }
        if sprite
        else None
    )
    with_pic = sum(1 for r in ordered if sprite_payload and r["fill"] in sprite_payload["index"])

    html = HTML_TEMPLATE
    html = html.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__SPRITE__", json.dumps(sprite_payload, ensure_ascii=False, separators=(",", ":")) if sprite_payload else "null")
    html = html.replace("__NEW_MIN__", str(LEGACY_MAX_ID))
    html = html.replace("__TOTAL__", str(len(uniq)))
    html = html.replace("__NEW__", str(new_count))
    html = html.replace("__EMOJI__", str(len(emoji_rows)))
    html = html.replace("__PIC__", str(with_pic))
    html = html.replace("__PIC_NOTE__", f"（{with_pic} 个带预览图）" if with_pic else "")
    html = html.replace("__FETCHED__", meta["fetched_at"])
    return html


def diff_snapshot(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """对比实时目录与内置快照，返回 (新增, 消失) 两组行。

    只在数据源是实时目录时有意义；调用方负责先判断 ``is_snapshot``。
    """

    if not SNAPSHOT_PATH.exists():
        return [], []

    snap_rows, _ = load_rows(SNAPSHOT_PATH)
    live = {r["sid"]: r for r in rows}
    snap = {r["sid"]: r for r in snap_rows}
    added = [live[sid] for sid in live if sid not in snap]
    removed = [snap[sid] for sid in snap if sid not in live]

    def order(row: dict[str, Any]) -> tuple[int, int]:
        return (0 if row["kind"] == "face" else 1, row["id"])

    return sorted(added, key=order), sorted(removed, key=order)


def print_diff(added: list[dict[str, Any]], removed: list[dict[str, Any]], limit: int = 40) -> None:
    """把差异打成「比内置快照新增 / 消失」。"""

    print()
    if not added and not removed:
        print("对比内置快照：无变化。")
        return

    print(f"对比内置快照：新增 {len(added)} 个，消失 {len(removed)} 个。")

    if added:
        print("  新增：")
        for row in added[:limit]:
            tag = "face id" if row["kind"] == "face" else "码点"
            print(f"    + {row['name']}  {tag}={row['fill']}  [{row['pack']}]")
        if len(added) > limit:
            print(f"    …… 还有 {len(added) - limit} 个")

    if removed:
        print("  消失（多半是限时包过期）：")
        for row in removed[:limit]:
            print(f"    - {row['name']}  {row['sid']}  [{row['pack']}]")
        if len(removed) > limit:
            print(f"    …… 还有 {len(removed) - limit} 个")

    print("  想让内置快照跟上：python tools/gen_face_table.py --update-snapshot")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 QQ 表情 ID 大全")
    parser.add_argument("--catalog", help="sys-face-catalog.json 路径")
    parser.add_argument("--out-dir", help="输出目录，默认插件 docs/")
    parser.add_argument("--check", action="store_true", help="只打印统计，不写文件")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="对比内置快照，列出新增/消失的表情（搭配 --check 只看不写）",
    )
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="用实时目录刷新内置快照 tools/face_catalog_snapshot.json（需要 SnowLuma）",
    )
    args = parser.parse_args()

    catalog, is_snapshot = resolve_catalog(args.catalog)

    if args.update_snapshot:
        n_packs, n_items = write_snapshot(catalog)
        print(f"已刷新内置快照 {SNAPSHOT_PATH.name}：{n_packs} 个包 / {n_items} 条")
        print()

    rows, meta = load_rows(catalog)
    meta["source_kind"] = (
        "插件内置的表情目录快照" if is_snapshot else "SnowLuma 缓存的 QQ 系统表情目录"
    )
    # 写进 Markdown 的版本带官网链接，用了人家的数据得标清楚
    meta["source_md"] = (
        "SnowLuma 表情目录的插件内置快照（[SnowLuma](https://snowluma.github.io/zh/)）"
        if is_snapshot
        else "[SnowLuma](https://snowluma.github.io/zh/) 缓存的 QQ 系统表情目录"
    )

    face_rows = [r for r in rows if r["kind"] == "face"]
    emoji_rows = [r for r in rows if r["kind"] == "emoji"]
    uniq = {r["id"] for r in face_rows}
    newer = {r["id"] for r in face_rows if r["new"]}

    print(f"数据源   : {catalog}")
    print(f"           ({meta['source_kind']})")
    print(f"抓取时间 : {meta['fetched_at']}")
    print(f"表情包   : {len(meta['packs'])} 个 -> {', '.join(meta['packs'])}")
    print(f"去重记录 : {len(rows)} 条")
    print(f"face id  : {len(uniq)} 个（{min(uniq)} ~ {max(uniq)}）")
    print(f"其中新版 : {len(newer)} 个（>{LEGACY_MAX_ID}）")
    print(f"emoji 码点: {len(emoji_rows)} 个（填 Unicode 十进制，不是 face id）")

    if is_snapshot:
        print()
        print("提示：没找到 SnowLuma 的实时目录，用的是插件自带快照 —— 表照样可用，")
        print("      NapCat / Lagrange / LLOneBot 用户不用装 SnowLuma。")
        print("      QQ 再出新表情时，需要有 SnowLuma 的环境跑 --update-snapshot 刷新。")
        if args.diff:
            print()
            print("对比内置快照：当前数据源就是快照本身，无从对比。")
    elif args.diff:
        print_diff(*diff_snapshot(rows))

    if args.check:
        return 0

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else Path(__file__).resolve().parents[1] / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "qq-face-ids.md"
    html_path = out_dir / "face-lookup.html"
    md_path.write_text(render_markdown(rows, meta), encoding="utf-8")
    html_path.write_text(render_html(rows, meta), encoding="utf-8")

    print()
    print(f"已写出 {md_path}  ({md_path.stat().st_size:,} 字节)")
    print(f"已写出 {html_path}  ({html_path.stat().st_size:,} 字节)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
