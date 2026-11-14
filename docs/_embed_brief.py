"""把汇报页里的截图占位符替换为内嵌 JPEG base64，得到单文件 HTML。"""
from __future__ import annotations

import base64
import io
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "docs", "brief-assets")
HTML = os.path.join(os.path.dirname(ROOT), "CBETS-B2C-演示汇报.html")

with open(HTML, encoding="utf-8") as f:
    html = f.read()

for name in sorted(os.listdir(ASSETS)):
    if not name.endswith(".png"):
        continue
    key = "__IMG_" + name[:2] + "__"
    if key not in html:
        continue
    im = Image.open(os.path.join(ASSETS, name)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=74, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    html = html.replace(key, "data:image/jpeg;base64," + b64)
    print(name, "->", len(buf.getvalue()) // 1024, "KB")

assert "__IMG_" not in html, "仍有未替换的图片占位符"
with open(HTML, "w", encoding="utf-8") as f:
    f.write(html)
print("HTML size KB:", os.path.getsize(HTML) // 1024)
