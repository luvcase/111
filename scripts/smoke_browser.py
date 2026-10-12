"""Validate the live demo workflow across desktop and mobile Chromium contexts."""

import argparse
import io
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    with httpx.Client(timeout=5, trust_env=False) as client:
        if not client.get(args.server + "/healthz").json()["demo"]:
            parser.error("Browser smoke requires serve --demo; it does not test real AI.")
    runtime = Path(".runtime")
    token = (runtime / "pair-token.txt").read_text().strip()
    picture = io.BytesIO()
    Image.new("RGB", (500, 200), "white").save(picture, format="PNG")
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium)
        computer = browser.new_context(
            viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Shanghai", locale="zh-CN"
        )
        phone = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
        )
        page, mobile = computer.new_page(), phone.new_page()
        for p in [page, mobile]:
            p.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(args.server + "/#token=" + quote(token))
        expect(page.locator("#connection")).to_have_text("● 已连接")
        expect(page.locator("#demo-banner")).to_be_visible()
        assert "token=" not in page.url
        username = "smoke_" + str(time.time_ns())
        page.locator("#account-button").click()
        page.locator("#username").fill(username)
        page.locator("#password").fill("smoke-password-123")
        page.get_by_role("button", name="注册新账号", exact=True).click()
        expect(page.locator("#account-button")).to_have_text(username)
        mobile.goto(args.server)
        mobile.locator("#login-from-pair").click()
        mobile.locator("#username").fill(username)
        mobile.locator("#password").fill("smoke-password-123")
        mobile.get_by_role("button", name="登录", exact=True).click()
        expect(mobile.locator("#account-button")).to_have_text(username)
        expect(mobile.locator("#connection")).to_have_text("● 已连接")
        page.locator("#new-model").click()
        page.locator("#model-name").fill("对照模型")
        page.locator("#model-name-api").fill("demo-model-b")
        page.locator("#model-form").get_by_role("button", name="保存配置").click()
        expect(page.locator(".model-row")).to_have_count(2)
        expect(mobile.locator(".model-row")).to_have_count(2)
        page.locator(".model-row").last.get_by_role("button", name="测试连接").click()
        expect(page.locator(".model-row").last.locator(".model-test-result")).to_contain_text(
            "未验证真实"
        )
        page.locator(".model-row").last.locator("input[type=checkbox]").check()
        expect(mobile.locator(".model-row").last.locator("input[type=checkbox]")).to_be_checked()
        page.locator("#collaborate").check()
        page.locator("#mode").select_option("code")
        expect(mobile.locator("#mode")).to_have_value("code")
        page.locator("#image-input").set_input_files(
            {"name": "question.png", "mimeType": "image/png", "buffer": picture.getvalue()}
        )
        expect(page.locator("#image-preview")).to_be_visible()
        page.locator("#crop-image").click()
        canvas = page.locator("#crop-canvas")
        expect(canvas).to_be_visible()
        rect = canvas.bounding_box()
        page.mouse.move(rect["x"] + 20, rect["y"] + 20)
        page.mouse.down()
        page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
        page.mouse.up()
        expect(page.locator("#crop-dialog")).not_to_be_visible()
        page.locator("#analyze").click()
        expect(page.locator(".job .badge")).to_have_text("已完成", timeout=10000)
        expect(page.locator(".comparison .model-result")).to_have_count(2)
        expect(page.locator(".synthesis")).to_be_visible()
        expect(mobile.locator(".comparison .model-result")).to_have_count(2)
        expect(mobile.locator(".job .badge")).to_have_text("已完成")
        expect(mobile.locator(".synthesis")).to_be_visible()
        mobile.reload()
        expect(mobile.locator("#connection")).to_have_text("● 已连接")
        expect(mobile.locator(".job .badge")).to_have_text("已完成")
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(runtime / "workbench-demo.png"), full_page=True)
        mobile.screenshot(path=str(runtime / "mobile-demo.png"), full_page=True)
        page.locator("#ocr").click()
        expect(page.locator(".job .badge")).to_have_text("已完成")
        expect(page.locator("#question")).to_have_value(
            "演示转录 · 未识别图片\n\n演示转录 · 未识别图片"
        )
        page.locator("#clear-image").click()
        page.locator("#question").fill("计算 1 + 1，并说明过程。")
        page.locator("#analyze").click()
        expect(mobile.locator(".job > .question-text")).to_have_text("计算 1 + 1，并说明过程。")
        expect(mobile.locator(".job .badge")).to_have_text("已完成")
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="删除此记录与题图", exact=True).click()
        expect(mobile.locator("#history-list button")).to_have_count(2)
        mobile.locator("#disconnect").click()
        expect(mobile.locator("#pairing")).to_be_visible()
        assert not errors, errors
        browser.close()
    print(
        "Browser smoke passed: accounts, cross-device settings/history, model CRUD/test, crop, "
        "parallel analysis, synthesis, OCR, reload and mobile layout."
    )


if __name__ == "__main__":
    main()
