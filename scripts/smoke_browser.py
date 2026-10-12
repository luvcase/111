"""Validate the running demo service with a mobile-sized Chromium browser."""

import argparse
import io
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    runtime = Path(".runtime")
    token = (runtime / "pair-token.txt").read_text().strip()
    picture = io.BytesIO()
    Image.new("RGB", (500, 200), "white").save(picture, format="PNG")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium)
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.server + "/#token=" + quote(token))
        expect(page.locator("#connection")).to_have_text("● 已连接")
        expect(page.locator("#demo-banner")).to_be_visible()
        assert "token=" not in page.url
        page.locator("#mode").select_option("code")
        page.locator("#image-input").set_input_files(
            {"name": "question.png", "mimeType": "image/png", "buffer": picture.getvalue()}
        )
        expect(page.locator(".job").first.locator(".badge")).to_have_text("已完成")
        expect(page.locator(".job").first.locator(".answer")).to_contain_text("未调用 AI")
        page.reload()
        expect(page.locator("#connection")).to_have_text("● 已连接")
        expect(page.locator(".job").first.locator(".badge")).to_have_text("已完成")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(runtime / "mobile-demo.png"), full_page=True)
        page.locator("#disconnect").click()
        expect(page.locator("#pairing")).to_be_visible()
        assert not errors, errors
        browser.close()
    print("Mobile browser smoke passed: pairing, upload, live answer, reload, layout, disconnect.")


if __name__ == "__main__":
    main()
