"""
Genera thumbnail per Etsy listing usando Playwright.
Ogni prodotto necessita di 3 immagini:
1. Cover PNG (1500x2000px) - prima immagine listing
2. Interior page PNG (1500x2000px) - anteprima contenuto
3. Dimensional mockup (2000x2000px) con badge "Instant Download"

Richiede: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import asyncio
import base64
import html
from pathlib import Path
from typing import Any


# Google Fonts mapping per HTML rendering
GOOGLE_FONT_MAP = {
    "PlayfairDisplay": "Playfair+Display",
    "Lato": "Lato",
    "Raleway": "Raleway",
    "JosefinSans": "Josefin+Sans",
    "Helvetica": "Lato",       # fallback
    "Times-Roman": "Playfair+Display",  # fallback
}


async def generate_pdf_thumbnail(
    pdf_path: Path,
    output_dir: Path,
    preset: str,
    preset_data: dict[str, Any],
    niche: str,
    colors: dict[str, str],
) -> dict[str, Path | str | list | None]:
    """
    Genera le 3 thumbnail Etsy per un PDF.

    Returns:
        {
            "cover": Path | None,
            "interior": Path | None,
            "mockup": Path | None,
            "errors": list[str]
        }
    """
    results: dict[str, Path | str | list | None] = {
        "cover": None,
        "interior": None,
        "mockup": None,
        "errors": [],
    }
    errors: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        errors.append(
            "playwright not installed: run 'pip install playwright && playwright install chromium'"
        )
        results["errors"] = errors
        return results

    cover_html = _build_cover_html(niche, preset, preset_data, colors)
    interior_html = _build_interior_html(niche, preset, preset_data, colors, pdf_path)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        try:
            # 1. Cover image (1500x2000)
            page = await browser.new_page(viewport={"width": 1500, "height": 2000})
            await page.set_content(cover_html)
            await page.wait_for_timeout(500)
            cover_path = output_dir / "thumbnail_cover.png"
            await page.screenshot(path=str(cover_path), full_page=False)
            results["cover"] = cover_path
            await page.close()

            # 2. Interior preview (1500x2000)
            page = await browser.new_page(viewport={"width": 1500, "height": 2000})
            await page.set_content(interior_html)
            await page.wait_for_timeout(500)
            interior_path = output_dir / "thumbnail_interior.png"
            await page.screenshot(path=str(interior_path), full_page=False)
            results["interior"] = interior_path
            await page.close()

            # 3. Dimensional mockup (2000x2000)
            mockup_html = _build_mockup_html(niche, preset, preset_data, colors, cover_path)
            page = await browser.new_page(viewport={"width": 2000, "height": 2000})
            await page.set_content(mockup_html)
            await page.wait_for_timeout(500)
            mockup_path = output_dir / "thumbnail_mockup.png"
            await page.screenshot(path=str(mockup_path), full_page=False)
            results["mockup"] = mockup_path
            await page.close()

        except Exception as e:
            errors.append(f"Screenshot error: {e}")
        finally:
            await browser.close()

    results["errors"] = errors
    return results


def _build_cover_html(
    niche: str,
    preset: str,
    preset_data: dict[str, Any],
    colors: dict[str, str],
) -> str:
    font_name = preset_data.get("font_heading", "Lato")
    google_font = GOOGLE_FONT_MAP.get(font_name, "Lato")

    bg = html.escape(colors.get("bg", preset_data.get("bg_color", "#FFFFFF")))
    text = html.escape(colors.get("text", preset_data.get("text_color", "#1A1A1A")))
    accent = html.escape(colors.get("accent", preset_data.get("accent_color", "#4A4A4A")))
    niche = html.escape(niche)

    decorative_elements = ""
    if preset == "decorative":
        decorative_elements = f"""
        <div style="position:absolute;top:40px;left:40px;width:80px;height:80px;border-top:3px solid {accent};border-left:3px solid {accent};"></div>
        <div style="position:absolute;top:40px;right:40px;width:80px;height:80px;border-top:3px solid {accent};border-right:3px solid {accent};"></div>
        <div style="position:absolute;bottom:40px;left:40px;width:80px;height:80px;border-bottom:3px solid {accent};border-left:3px solid {accent};"></div>
        <div style="position:absolute;bottom:40px;right:40px;width:80px;height:80px;border-bottom:3px solid {accent};border-right:3px solid {accent};"></div>
        <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:400px;height:1px;background:{accent};opacity:0.3;"></div>
        """
    elif preset == "playful":
        decorative_elements = f"""
        <div style="position:absolute;top:20px;left:20px;width:60px;height:60px;border-radius:50%;border:4px solid {accent};opacity:0.6;"></div>
        <div style="position:absolute;top:20px;right:20px;width:60px;height:60px;border-radius:50%;border:4px solid {accent};opacity:0.6;"></div>
        <div style="position:absolute;bottom:20px;left:20px;width:60px;height:60px;border-radius:50%;border:4px solid {accent};opacity:0.6;"></div>
        <div style="position:absolute;bottom:20px;right:20px;width:60px;height:60px;border-radius:50%;border:4px solid {accent};opacity:0.6;"></div>
        """

    font_family = google_font.replace("+", " ")
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family={google_font}:wght@400;700&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    width: 1500px; height: 2000px;
    background: {bg};
    font-family: '{font_family}', sans-serif;
    display: flex; align-items: center; justify-content: center;
    position: relative; overflow: hidden;
}}
.container {{
    text-align: center;
    padding: 80px;
    position: relative;
    z-index: 2;
}}
.badge {{
    background: {accent};
    color: white;
    padding: 12px 32px;
    border-radius: 4px;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin-bottom: 60px;
    display: inline-block;
}}
h1 {{
    font-size: 96px;
    font-weight: 700;
    color: {text};
    line-height: 1.1;
    margin-bottom: 40px;
}}
.subtitle {{
    font-size: 42px;
    color: {accent};
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 60px;
}}
.divider {{
    width: 200px;
    height: 2px;
    background: {accent};
    margin: 40px auto;
    opacity: 0.6;
}}
.tagline {{
    font-size: 32px;
    color: {text};
    opacity: 0.7;
    font-style: italic;
}}
</style>
</head>
<body>
{decorative_elements}
<div class="container">
    <div class="badge">&#10022; Digital Download &#10022;</div>
    <h1>{niche.title()}</h1>
    <div class="divider"></div>
    <div class="subtitle">Printable Template</div>
    <div class="tagline">Instant Download &middot; Print at Home &middot; A4 &amp; US Letter</div>
</div>
</body>
</html>"""


def _build_interior_html(
    niche: str,
    preset: str,
    preset_data: dict[str, Any],
    colors: dict[str, str],
    pdf_path: Path,
) -> str:
    font_name = preset_data.get("font_primary", "Lato")
    google_font = GOOGLE_FONT_MAP.get(font_name, "Lato")

    bg = html.escape(colors.get("bg", "#FFFFFF"))
    text = html.escape(colors.get("text", "#1A1A1A"))
    accent = html.escape(colors.get("accent", "#4A4A4A"))
    niche = html.escape(niche)

    rows = ""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    for i in range(20):
        rows += (
            f'<div style="border-bottom:1px solid {accent}22;height:60px;'
            f'display:flex;align-items:center;padding:0 20px;'
            f'font-size:24px;color:{text}66;">'
            f'{day_names[i % 5]}</div>'
        )

    font_family = google_font.replace("+", " ")
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family={google_font}:wght@400;700&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    width: 1500px; height: 2000px;
    background: white;
    font-family: '{font_family}', sans-serif;
    padding: 60px;
}}
.page {{
    width: 100%; height: 100%;
    background: {bg};
    border: 1px solid {accent}33;
    box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    padding: 80px;
}}
.header {{
    border-bottom: 2px solid {accent};
    padding-bottom: 30px;
    margin-bottom: 40px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.page-title {{
    font-size: 56px;
    font-weight: 700;
    color: {text};
}}
.week-label {{
    font-size: 32px;
    color: {accent};
    text-transform: uppercase;
    letter-spacing: 2px;
}}
</style>
</head>
<body>
<div class="page">
    <div class="header">
        <div class="page-title">{niche.title()} Planner</div>
        <div class="week-label">Week _____</div>
    </div>
    {rows}
</div>
</body>
</html>"""


def _build_mockup_html(
    niche: str,
    preset: str,
    preset_data: dict[str, Any],
    colors: dict[str, str],
    cover_path: Path,
) -> str:
    accent = html.escape(colors.get("accent", "#4A4A4A"))

    img_data = ""
    if cover_path and cover_path.exists():
        with open(cover_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        img_data = f"data:image/png;base64,{img_b64}"

    img_tag = (
        f'<img class="pdf-preview" src="{img_data}">'
        if img_data
        else '<div class="pdf-preview" style="background:#ddd;"></div>'
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Lato:wght@400;700&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    width: 2000px; height: 2000px;
    background: linear-gradient(135deg, #f5f5f0 0%, #e8e8e0 100%);
    display: flex; align-items: center; justify-content: center;
    font-family: 'Lato', sans-serif;
    position: relative;
}}
.mockup-container {{
    position: relative;
    transform: perspective(800px) rotateY(-8deg) rotateX(3deg);
    box-shadow: 40px 40px 80px rgba(0,0,0,0.25);
}}
.pdf-preview {{
    width: 750px;
    height: 1000px;
    object-fit: cover;
    display: block;
}}
.instant-badge {{
    position: absolute;
    bottom: -30px;
    right: -30px;
    background: {accent};
    color: white;
    padding: 20px 40px;
    font-size: 32px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    box-shadow: 0 8px 20px rgba(0,0,0,0.3);
    transform: rotate(-3deg);
}}
.stars {{
    position: absolute;
    top: -20px;
    left: 50%;
    transform: translateX(-50%);
    background: white;
    padding: 10px 30px;
    border-radius: 30px;
    font-size: 28px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    white-space: nowrap;
    color: #333;
    font-weight: 700;
}}
</style>
</head>
<body>
<div class="mockup-container">
    {img_tag}
    <div class="stars">&#11088;&#11088;&#11088;&#11088;&#11088; Instant Download</div>
    <div class="instant-badge">&#9889; Instant Download</div>
</div>
</body>
</html>"""
