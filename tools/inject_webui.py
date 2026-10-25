#!/usr/bin/env python3
"""把「插件配置页美化」资源注入 MaiBot Dashboard。

跨平台：Windows / Linux / macOS 都适用，只依赖标准库。
原理：把 webui/ 下的 css / js 复制到 maibot_dashboard 包的 dist/assets/，
      再在 dist/index.html 的 </head> 前插入一对带标记的标签（可重复执行，
      不会重复插入；每次执行都会刷新 ?v= 时间戳，避免浏览器强缓存）。

用法（必须在 MaiBot 运行所用的 Python 环境里执行）：
    python tools/inject_webui.py            # 注入 / 更新
    python tools/inject_webui.py --check    # 只检查当前状态
    python tools/inject_webui.py --remove   # 卸载（删标签，保留资源文件）

注意：dashboard 是通过 pip 安装的包，**升级 maibot_dashboard 后这些改动会丢**
（dist 被替换），升级后重新跑一次本脚本即可。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

BEGIN = "<!-- mb-plugin-config:start -->"
END = "<!-- mb-plugin-config:end -->"
ASSETS = ("mb-plugin-config.css", "mb-plugin-config.js")
BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)

SCRIPT_DIR = Path(__file__).resolve().parent
WEBUI_DIR = SCRIPT_DIR.parent / "webui"


def find_dist() -> Path:
    """定位 maibot_dashboard 的 dist 目录。"""

    try:
        import maibot_dashboard  # noqa: PLC0415
    except ImportError:
        sys.exit(
            "找不到 maibot_dashboard 包。\n"
            "请在 MaiBot 实际运行所用的 Python 环境里执行本脚本，例如：\n"
            "  Windows 一键包：\"%APPDATA%\\MaiBotOneKeyDesktop\\<id>\\python-env\\python.exe\" tools/inject_webui.py\n"
            "  Linux venv   ：/path/to/venv/bin/python tools/inject_webui.py"
        )

    dist = Path(maibot_dashboard.__file__).resolve().parent / "dist"
    index = dist / "index.html"
    if not index.exists():
        sys.exit(f"dist 目录里没有 index.html：{index}")
    return dist


def read_index(index: Path) -> str:
    return index.read_text(encoding="utf-8")


def build_block(stamp: str) -> str:
    lines = [
        f"    {BEGIN}",
        "    <!-- 由 plugins/workbuddy_reply_confirm/tools/inject_webui.py 生成 -->",
        f'    <script src="/assets/mb-plugin-config.js?v={stamp}" defer></script>',
        f'    <link rel="stylesheet" href="/assets/mb-plugin-config.css?v={stamp}">',
        f"    {END}",
    ]
    return "\n".join(lines)


def copy_assets(dist: Path) -> list[Path]:
    assets_dir = dist / "assets"
    if not assets_dir.exists():
        sys.exit(f"dist 下没有 assets 目录：{assets_dir}")

    copied: list[Path] = []
    for name in ASSETS:
        src = WEBUI_DIR / name
        if not src.exists():
            sys.exit(f"缺少资源文件：{src}")
        dst = assets_dir / name
        shutil.copyfile(src, dst)
        copied.append(dst)
    return copied


def status(html: str) -> str:
    match = BLOCK_RE.search(html)
    if not match:
        return "未注入"
    stamp = re.search(r"\?v=(\d+)", match.group(0))
    return f"已注入（版本戳 {stamp.group(1) if stamp else '无'}）"


def main() -> int:
    parser = argparse.ArgumentParser(description="注入 / 移除插件配置页美化资源")
    parser.add_argument("--check", action="store_true", help="只检查状态，不改文件")
    parser.add_argument("--remove", action="store_true", help="移除注入的标签")
    args = parser.parse_args()

    dist = find_dist()
    index = dist / "index.html"
    html = read_index(index)

    print(f"dist     : {dist}")
    print(f"当前状态 : {status(html)}")

    if args.check:
        return 0

    if args.remove:
        new_html = BLOCK_RE.sub("", html).rstrip() + "\n"
        if new_html == html:
            print("没有可移除的内容。")
            return 0
        index.write_text(new_html, encoding="utf-8")
        print("已移除注入标签（assets 里的资源文件保留，可手动删除）。")
        return 0

    copied = copy_assets(dist)
    for path in copied:
        print(f"已复制   : {path}")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    block = build_block(stamp)

    if BLOCK_RE.search(html):
        new_html = BLOCK_RE.sub(block, html, count=1)
    else:
        if "</head>" not in html:
            sys.exit("index.html 里没有 </head>，无法插入。")
        new_html = html.replace("</head>", block + "\n  </head>", 1)

    if new_html == html:
        print("内容无变化。")
        return 0

    index.write_text(new_html, encoding="utf-8")
    print(f"已写入   : {index}")
    print(f"版本戳   : {stamp}（浏览器会重新拉取，不会用缓存）")
    print("完成。刷新 WebUI 的「插件配置」页即可看到效果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
