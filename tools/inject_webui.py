#!/usr/bin/env python3
"""手动注入 / 移除「插件配置页美化」（兜底用）。

平时**不需要**跑这个 —— 插件配置里的「美化配置页」开关就是干这个的，
拨一下自动生效。只有在插件没加载、或者想强制重建时才手动执行。

必须在 MaiBot 实际运行所用的 Python 环境里跑（要找得到 maibot_dashboard）。

用法：
    python tools/inject_webui.py            # 注入 / 更新
    python tools/inject_webui.py --check    # 只看状态
    python tools/inject_webui.py --remove   # 移除
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def load_injector():
    """加载插件根目录的 webui_inject.py。"""

    path = PLUGIN_DIR / "webui_inject.py"
    if not path.is_file():
        raise SystemExit(f"找不到 {path}")
    spec = importlib.util.spec_from_file_location("_mb_webui_inject", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"加载失败：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="注入 / 移除插件配置页美化资源")
    parser.add_argument("--check", action="store_true", help="只检查状态，不改文件")
    parser.add_argument("--remove", action="store_true", help="移除注入的标签")
    args = parser.parse_args()

    injector = load_injector()
    dist = injector.find_dist()
    if dist is None:
        raise SystemExit(
            "找不到 maibot_dashboard 的 dist。\n"
            "请在 MaiBot 实际运行所用的 Python 环境里执行，例如：\n"
            '  "%APPDATA%\\MaiBotOneKeyDesktop\\<id>\\python-env\\python.exe" tools/inject_webui.py'
        )

    print(f"dist     : {dist}")
    print(f"当前状态 : {injector.current_state(dist)}")
    if args.check:
        return 0

    try:
        if args.remove:
            changed, message = injector.remove(dist)
        else:
            changed, message = injector.inject(dist)
    except injector.InjectError as exc:
        raise SystemExit(f"操作失败：{exc}") from exc

    print(f"结果     : {message}")
    if changed:
        print("刷新 WebUI 的「插件配置」页即可看到效果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
