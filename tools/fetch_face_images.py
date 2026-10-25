#!/usr/bin/env python3
"""从 QQ 的表情资源包里抓图，拼成一张雪碧图，供速查器显示表情。

为什么要这一步
--------------
``sys-face-catalog.json`` 里每条都带 ``url``，指向一个 zip 包，包里是
``<qSid>/png/<qSid>.png``（原图 128×128 起步）。速查器想显示表情，就得把这些图
拿到手 —— 但 367 张单独内嵌成 base64 要 800KB 以上，拼成一张雪碧图后只有两三百 KB。

所以：下载 → 缩放 → 拼图 → 存成 ``tools/face_sprite.json``（含 base64 雪碧图 + 序号表）。
``gen_face_table.py`` 读到它就把表情内嵌进 HTML，读不到就生成不带图的版本。

什么时候要重跑
------------
QQ 出新表情、或者表情包换资源地址时。需要有带 ``url`` 字段的目录文件
（SnowLuma 的 ``sys-face-catalog.json`` 有；插件内置快照被裁过，没有 url）。

用法
----
    python tools/fetch_face_images.py                  # 抓全量，约 1~3 分钟
    python tools/fetch_face_images.py --check          # 只看有多少可抓
    python tools/fetch_face_images.py --only 478,14    # 只抓几个试试
    python tools/fetch_face_images.py --size 48        # 换缩略图尺寸
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    raise SystemExit(
        "需要 Pillow 来缩放拼图：\n"
        "  pip install Pillow\n"
        "或者用装了 Pillow 的虚拟环境跑这个脚本。"
    )

SNAPSHOT_PATH = Path(__file__).resolve().parent / "face_catalog_snapshot.json"
SPRITE_PATH = Path(__file__).resolve().parent / "face_sprite.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def resolve_catalog(explicit: str | None) -> Path:
    """找带 url 字段的目录文件。优先 SnowLuma 实时目录，其次同目录的完整副本。"""

    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"找不到目录文件：{path}")
        return path

    here = Path(__file__).resolve()
    parents = here.parents
    project_root = parents[4] if len(parents) > 4 else here.parent
    candidates = [
        project_root / "SnowLuma" / "data" / "sys-face-catalog.json",
        Path.cwd() / "SnowLuma" / "data" / "sys-face-catalog.json",
        Path.cwd() / "data" / "sys-face-catalog.json",
        here.parent / "sys-face-catalog.json",
        Path.home() / "SnowLuma" / "data" / "sys-face-catalog.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            first = next((p.get("emojis") or [None])[0] for p in (raw.get("packs") or []) if p.get("emojis"))
            if first and first.get("url"):
                return candidate
    raise SystemExit(
        "没找到带 url 字段的表情目录。\n"
        "插件内置快照被裁剪过、不含 url，抓图必须在有 SnowLuma 的机器上跑，\n"
        "或者用 --catalog 指定完整的 sys-face-catalog.json。"
    )


def collect_targets(path: Path) -> list[tuple[str, str]]:
    """取出 [(填的值, 资源 url)]，按 qSid 去重。"""

    raw = json.loads(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    targets: list[tuple[str, str]] = []
    for pack in raw.get("packs") or []:
        for item in pack.get("emojis") or []:
            sid = str(item.get("qSid") or "").strip()
            url = str(item.get("url") or "").strip()
            if not sid or not url or sid in seen:
                continue
            seen.add(sid)
            fill = sid if sid.isdigit() else str(ord(sid[0]))
            targets.append((fill, url))
    return targets


def fetch_bytes(url: str, retries: int = 2) -> bytes:
    """下载资源包，非 ASCII 路径要手动转义（emoji 类的 url 里带 emoji 字符）。"""

    safe_url = urllib.parse.quote(url, safe=":/?&=#%+")
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(safe_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - 单张失败不该中断整批
            last = exc
            if attempt == retries:
                break
    raise RuntimeError(f"{type(last).__name__}: {last}")


def make_thumb(raw: bytes, size: int) -> Image.Image:
    """从 zip 里取第一张 png，等比缩进 size×size 的透明画布。"""

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith((".png", ".webp", ".jpg", ".gif"))]
        if not names:
            raise ValueError("资源包里没有图片")
        # 优先取 <id>/png/<id>.png 这种标准路径，否则取第一个
        names.sort(key=lambda n: (n.count("/"), len(n)))
        image = Image.open(io.BytesIO(archive.read(names[0]))).convert("RGBA")

    image.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2), image)
    return canvas


def build_sprite(
    targets: list[tuple[str, str]], size: int, cols: int, workers: int
) -> tuple[Image.Image, dict[str, int], list[str]]:
    """并发抓图并拼成雪碧图。返回 (图, {值: 序号}, 失败列表)。"""

    rows = (len(targets) + cols - 1) // cols
    sprite = Image.new("RGBA", (cols * size, rows * size), (0, 0, 0, 0))
    index: dict[str, int] = {}
    failed: list[str] = []
    done = 0

    def work(item: tuple[int, str, str]) -> tuple[int, str, Image.Image | None, str]:
        order, fill, url = item
        try:
            return order, fill, make_thumb(fetch_bytes(url), size), ""
        except Exception as exc:  # noqa: BLE001
            return order, fill, None, str(exc)

    tasks = [(i, fill, url) for i, (fill, url) in enumerate(targets)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, task) for task in tasks]
        for future in as_completed(futures):
            order, fill, thumb, error = future.result()
            done += 1
            if thumb is None:
                failed.append(f"{fill}（{error[:60]}）")
            else:
                sprite.paste(thumb, ((order % cols) * size, (order // cols) * size), thumb)
                index[fill] = order
            if done % 40 == 0 or done == len(tasks):
                print(f"  进度 {done}/{len(tasks)}", flush=True)

    return sprite, index, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="抓 QQ 表情图并拼雪碧图")
    parser.add_argument("--catalog", help="带 url 的表情目录 json 路径")
    parser.add_argument("--out", help="输出路径，默认 tools/face_sprite.json")
    parser.add_argument("--size", type=int, default=40, help="单格像素，默认 40")
    parser.add_argument("--cols", type=int, default=20, help="每行几格，默认 20")
    parser.add_argument("--workers", type=int, default=8, help="并发数，默认 8")
    parser.add_argument("--only", help="只抓指定值，逗号分隔，例如 478,14")
    parser.add_argument("--check", action="store_true", help="只统计，不下载")
    args = parser.parse_args()

    catalog = resolve_catalog(args.catalog)
    targets = collect_targets(catalog)

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        targets = [t for t in targets if t[0] in wanted]
        if not targets:
            raise SystemExit(f"--only 里的值在目录里一个都没找到：{args.only}")

    print(f"数据源 : {catalog}")
    print(f"待抓取 : {len(targets)} 个表情")
    print(f"规格   : {args.size}×{args.size} / 每行 {args.cols} 格")
    if args.check:
        return 0

    print("开始下载 ...")
    sprite, index, failed = build_sprite(targets, args.size, args.cols, args.workers)

    rows = (len(targets) + args.cols - 1) // args.cols
    buffer = io.BytesIO()
    sprite.save(buffer, "WEBP", quality=85, method=6)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    output = Path(args.out).expanduser().resolve() if args.out else SPRITE_PATH
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "size": args.size,
        "cols": args.cols,
        "rows": rows,
        "count": len(index),
        "note": "QQ 系统表情雪碧图。序号 → 第 (序号%cols, 序号//cols) 格。由 tools/fetch_face_images.py 生成。",
        "index": index,
        "image": f"data:image/webp;base64,{encoded}",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    print()
    print(f"成功   : {len(index)} / {len(targets)}")
    if failed:
        print(f"失败   : {len(failed)} 个")
        for item in failed[:8]:
            print(f"   - {item}")
        if len(failed) > 8:
            print(f"   ... 还有 {len(failed) - 8} 个")
    print(f"雪碧图 : {args.cols * args.size}×{rows * args.size}px，webp {len(buffer.getvalue()):,} 字节")
    print(f"已写出 : {output}（{output.stat().st_size:,} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
