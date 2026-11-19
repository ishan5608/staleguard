"""Capture docs/screenshot.jpg of the UI running the offline demo.

Usage: start the server (STALEGUARD_MODE=offline uvicorn staleguard.server:app --port 8000)
then: pip install playwright && playwright install chromium && python scripts/screenshot.py
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshot.jpg"


async def main():
    OUT.parent.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1400, "height": 1000})
        await page.goto(URL)
        await page.click("#demo")
        await page.wait_for_timeout(300)
        await page.click("#go")
        await page.wait_for_selector(".ring")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT), type="jpeg", quality=85)
        await browser.close()
    print("wrote", OUT)


asyncio.run(main())
