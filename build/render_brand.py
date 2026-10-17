"""Render brand/logo.svg into the app icons (PNG sizes, Windows .ico) and brand/tray-template.svg into the macOS
menu-bar template (trayTemplate.png and @2x).

    python build/render_brand.py

Needs Python Playwright with Chromium (to rasterize the SVG) and Pillow (for the .ico). Outputs go to brand/out/ and
are committed, so a build machine without these tools still has the icons.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "brand" / "logo.svg"
TRAY_SOURCE = ROOT / "brand" / "tray-template.svg"
OUT = ROOT / "brand" / "out"
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(svg: str, size: int, page) -> bytes:
    page.set_viewport_size({"width": size, "height": size})
    sized = svg.replace("<svg ", '<svg width="%d" height="%d" ' % (size, size), 1)
    page.set_content(
        "<html><body style='margin:0;background:transparent'>"
        f"<div style='width:{size}px;height:{size}px'>{sized}</div>"
        "</body></html>"
    )
    return page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})


def main() -> int:
    svg = SOURCE.read_text(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    images: dict[int, Image.Image] = {}
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception:  # Playwright's own Chromium not downloaded: any installed Edge renders the same SVG.
            browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(device_scale_factor=1)
        for size in PNG_SIZES:
            data = render(svg, size, page)
            (OUT / f"icon-{size}.png").write_bytes(data)
            images[size] = Image.open(io.BytesIO(data)).convert("RGBA")
        tray = TRAY_SOURCE.read_text(encoding="utf-8")
        for name, size in (("trayTemplate.png", 16), ("trayTemplate@2x.png", 32)):
            (OUT / name).write_bytes(render(tray, size, page))
        browser.close()
    images[256].save(OUT / "icon.ico", sizes=[(size, size) for size in ICO_SIZES],
                     append_images=[images[size] for size in ICO_SIZES if size != 256])
    images[1024].save(OUT / "icon.png")
    print(f"rendered {len(PNG_SIZES)} PNG sizes, icon.ico and the tray template into {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
