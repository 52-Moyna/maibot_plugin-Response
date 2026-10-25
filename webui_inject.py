"""配置页美化的注入 / 移除。

MaiBot 的插件配置页是 dashboard 提供的，插件没法在运行时改它的布局，
只能把一小段 css / js 塞进 ``maibot_dashboard/dist`` 里，让它跟着页面一起加载。

**这会写到宿主程序的安装目录，默认不执行**：只有配置里 ``beautify_ui = true``
（用户主动打开）或手动跑 ``tools/inject_webui.py`` 才会动文件。
开关关着时插件加载完全不碰 dist 目录。

这个模块供两处复用：

- ``plugin.py``：配置里的「美化配置页」开关一开一关，插件自己调 :func:`inject` / :func:`remove`
- ``tools/inject_webui.py``：命令行兜底（插件没加载时也能手动跑）

所有函数都不调 ``sys.exit``，出错抛 :class:`InjectError`，由调用方决定怎么处理。
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

BEGIN = "<!-- mb-plugin-config:start -->"
END = "<!-- mb-plugin-config:end -->"
ASSETS = ("mb-plugin-config.css", "mb-plugin-config.js")
BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)

#: 插件根目录（本文件所在的目录）
PLUGIN_DIR = Path(__file__).resolve().parent
WEBUI_DIR = PLUGIN_DIR / "webui"


class InjectError(RuntimeError):
    """注入失败。"""


def find_dist() -> Path | None:
    """定位 ``maibot_dashboard/dist``。找不到返回 None（不算错误，可能没装 dashboard）。"""

    try:
        import maibot_dashboard  # noqa: PLC0415 - 延迟导入，脚本环境下才用得上
    except ImportError:
        return None
    dist = Path(maibot_dashboard.__file__).resolve().parent / "dist"
    return dist if (dist / "index.html").is_file() else None


def current_state(dist: Path) -> str:
    """当前注入状态，给人看的一句话。"""

    html = (dist / "index.html").read_text(encoding="utf-8")
    match = BLOCK_RE.search(html)
    if not match:
        return "未注入"
    stamp = re.search(r"\?v=(\d+)", match.group(0))
    return f"已注入（版本戳 {stamp.group(1) if stamp else '无'}）"


def _copy_assets(dist: Path) -> None:
    assets_dir = dist / "assets"
    if not assets_dir.is_dir():
        raise InjectError(f"dist 下没有 assets 目录：{assets_dir}")
    for name in ASSETS:
        source = WEBUI_DIR / name
        if not source.is_file():
            raise InjectError(f"缺少资源文件：{source}")
        shutil.copyfile(source, assets_dir / name)


def _build_block(stamp: str) -> str:
    return "\n".join(
        [
            f"    {BEGIN}",
            "    <!-- 由回复标记插件生成，可重复写入 -->",
            f'    <script src="/assets/mb-plugin-config.js?v={stamp}" defer></script>',
            f'    <link rel="stylesheet" href="/assets/mb-plugin-config.css?v={stamp}">',
            f"    {END}",
        ]
    )


def inject(dist: Path) -> tuple[bool, str]:
    """把美化资源注入 dist。返回 (是否有改动, 说明)。"""

    index = dist / "index.html"
    html = index.read_text(encoding="utf-8")
    _copy_assets(dist)

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    block = _build_block(stamp)
    if BLOCK_RE.search(html):
        new_html = BLOCK_RE.sub(block, html, count=1)
    else:
        if "</head>" not in html:
            raise InjectError("index.html 里没有 </head>，插不进去")
        new_html = html.replace("</head>", block + "\n  </head>", 1)

    if new_html == html:
        return False, "已是最新，没改动"
    index.write_text(new_html, encoding="utf-8")
    return True, f"已注入（版本戳 {stamp}）"


def remove(dist: Path) -> tuple[bool, str]:
    """摘掉注入的标签（assets 里的文件保留，无害）。返回 (是否有改动, 说明)。"""

    index = dist / "index.html"
    html = index.read_text(encoding="utf-8")
    if not BLOCK_RE.search(html):
        return False, "本来就没注入"
    index.write_text(BLOCK_RE.sub("", html).rstrip() + "\n", encoding="utf-8")
    return True, "已移除注入"
