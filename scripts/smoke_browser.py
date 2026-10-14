"""Validate the live demo workflow across desktop and mobile Chromium contexts."""

import argparse
import io
import json
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--runtime-dir", type=Path, default=Path(".runtime"))
    args = parser.parse_args()
    with httpx.Client(timeout=5, trust_env=False) as client:
        if not client.get(args.server + "/healthz").json()["demo"]:
            parser.error("Browser smoke requires serve --demo; it does not test real AI.")
    runtime = args.runtime_dir
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
        # Local grid preview exercises real pixel extraction, with no model mock.
        grid_picture = Image.new("RGB", (320, 220), "white")
        draw = ImageDraw.Draw(grid_picture)
        for r in range(4):
            for c in range(4):
                draw.rectangle(
                    (30 + c * 24, 30 + r * 24, 30 + (c + 1) * 24, 30 + (r + 1) * 24),
                    fill="black" if (r + c) % 2 == 0 else "white",
                )
        for k in range(5):
            draw.line((30, 30 + k * 24, 126, 30 + k * 24), fill="black")
            draw.line((30 + k * 24, 30, 30 + k * 24, 126), fill="black")
        grid_bytes = io.BytesIO()
        grid_picture.save(grid_bytes, "PNG")
        page.locator("#image-input").set_input_files(
            {"name": "grids.png", "mimeType": "image/png", "buffer": grid_bytes.getvalue()}
        )
        page.locator("#inspect-grids").click()
        expect(page.locator("#grid-summary")).to_contain_text("检测到 1 个网格")
        expect(page.locator("#grid-summary pre")).to_have_text("1 0 1 0\n0 1 0 1\n1 0 1 0\n0 1 0 1")
        expect(page.locator("#grid-image")).to_be_visible()
        page.screenshot(path=str(runtime / "local-grid-check.png"))
        page.locator("#grid-dialog .close-dialog").click()
        page.locator("#grid-reading").check()
        expect(mobile.locator("#grid-reading")).to_be_checked()
        page.locator("#analyze").click()
        expect(page.locator(".job .badge")).to_have_text("已完成", timeout=10000)
        page.get_by_text("本地读格数据", exact=True).click()
        expect(page.locator(".job pre")).to_have_text("1 0 1 0\n0 1 0 1\n1 0 1 0\n0 1 0 1")
        page.get_by_role("button", name="查看采样检查图").click()
        expect(page.locator("#grid-image")).to_be_visible()
        page.locator("#grid-dialog .close-dialog").click()
        page.locator("#grid-reading").uncheck()
        page.locator("#clear-image").click()
        # Verify the visible picker, credential-list UI and applied model across devices.
        # The provider listing is mocked here; server-side credential handling has API tests.
        page.route(
            "**/api/model-catalog",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"models": ["gpt-6-astra", "gpt-6.1-sol"], "message": "模拟账号列表"}
                ),
            ),
        )
        page.locator("#change-model").click()
        page.locator("#model-preset").select_option("gpt-6-astra")
        expect(page.locator("#model-name-api")).to_have_value("gpt-6-astra")
        expect(page.locator("#reasoning-effort")).to_have_value("high")
        page.locator("#load-catalog").click()
        expect(page.locator("#available-model")).to_be_enabled()
        page.locator("#available-model").select_option("gpt-6.1-sol")
        page.locator("#model-form").get_by_role("button", name="保存并应用").click()
        expect(page.locator("#active-model")).to_contain_text("gpt-6.1-sol")
        expect(mobile.locator("#active-model")).to_contain_text("gpt-6.1-sol")
        mobile.locator("#change-model").click()
        expect(mobile.locator("#model-preset")).to_have_value("gpt-6.1-sol")
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(runtime / "model-picker-demo.png"), full_page=True)
        mobile.locator("#model-dialog .close-dialog").click()
        page.locator("#new-model").click()
        page.locator("#model-name").fill("对照模型")
        page.locator("#model-name-api").fill("demo-model-b")
        page.locator("#speed-preset").click()
        expect(page.locator("#output-budget")).to_have_value("2048")
        expect(page.locator("#compact-output")).to_be_checked()
        page.locator("#qwen-thinking").select_option("off")
        page.locator("#request-timeout").fill("270")
        page.locator("#model-form").get_by_role("button", name="保存并应用").click()
        expect(page.locator(".model-row")).to_have_count(2)
        expect(mobile.locator(".model-row")).to_have_count(2)
        page.locator(".model-row").last.get_by_role("button", name="编辑", exact=True).click()
        expect(page.locator("#output-budget")).to_have_value("2048")
        expect(page.locator("#compact-output")).to_be_checked()
        expect(page.locator("#qwen-thinking")).to_have_value("off")
        expect(page.locator("#request-timeout")).to_have_value("270")
        page.locator("#model-dialog .close-dialog").click()
        page.locator(".model-row").last.get_by_role("button", name="测试连接").click()
        expect(page.locator(".model-row").last.locator(".model-test-result")).to_contain_text(
            "未验证真实"
        )
        page.locator(".model-row").first.locator("input[type=checkbox]").check()
        expect(mobile.locator(".model-row").first.locator("input[type=checkbox]")).to_be_checked()
        expect(mobile.locator(".model-row").last.locator("input[type=checkbox]")).to_be_checked()
        page.locator("#collaborate").check()
        page.locator("#mode").select_option("code")
        expect(mobile.locator("#mode")).to_have_value("code")
        page.locator("#image-input").set_input_files(
            {"name": "question.png", "mimeType": "image/png", "buffer": picture.getvalue()}
        )
        expect(page.locator("#image-preview")).to_be_visible()
        page.locator("#view-original").click()
        expect(page.locator("#original-info")).to_contain_text("500 × 200")
        expect(page.locator("#original-image")).to_be_visible()
        page.locator("#original-dialog .close-dialog").click()
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
        expect(page.locator(".comparison .model-result").first).to_contain_text("gpt-6.1-sol")
        expect(page.locator(".synthesis")).to_be_visible()
        expect(mobile.locator(".comparison .model-result")).to_have_count(2)
        expect(mobile.locator(".job .badge")).to_have_text("已完成")
        expect(mobile.locator(".comparison .model-result").first.locator(".answer")).to_be_visible()
        assert (
            mobile.locator(".comparison .model-result").first.locator(".answer").bounding_box()["y"]
            < 300
        )
        expect(
            mobile.locator(".comparison .model-result").first.locator(".question-text")
        ).not_to_be_visible()
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
        long_question = (
            "给定材料：社区通过协商改善居民服务。" * 100 + "请概括主要做法，不超过200字。"
        )
        page.locator("#question-type").select_option("shenlun")
        expect(page.locator("#mode")).to_have_value("shenlun")
        expect(mobile.locator("#mode")).to_have_value("shenlun")
        mobile.evaluate("window.scrollTo(0,document.body.scrollHeight)")
        page.locator("#question").fill(long_question)
        page.locator("#analyze").click()
        expect(mobile.locator(".job > details .question-text")).to_have_text(long_question)
        expect(mobile.locator(".job > details .question-text")).not_to_be_visible()
        expect(mobile.locator(".job .badge")).to_have_text("已完成")
        answer = mobile.locator(".comparison .model-result").first.locator(".answer")
        expect(answer).to_be_visible()
        assert 0 <= answer.bounding_box()["y"] < 300
        mobile.screenshot(path=str(runtime / "mobile-answer-first.png"))
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="删除此记录与题图", exact=True).click()
        # The added grid-analysis job also remains in history.
        expect(mobile.locator("#history-list button")).to_have_count(3)
        # Exercise failed-result diagnostics with a simulated timeout, without a real API call.
        mobile.evaluate("""() => {
          const job={id:'timeout-ui-fixture',created_at:Date.now()/1000,
            status:'error',mode:'visual',operation:'analyze',results:[{
              model_name:'千问',requested_model:'qwen3.8-omni-flash',status:'error',
              error:'AI 请求超时：等待接口回复',elapsed_ms:180000,diagnostics:{
                endpoint_host:'ws-fixture.cn-beijing.maas.aliyuncs.com',
                api_ms:180000,retry_wait_ms:0,attempts:1,output_budget:2048,
                qwen_thinking:'auto',compact_output:true,read_timeout_seconds:180,
                timeout_phase:'等待接口回复',model_confirmed:false
              }}]};
          records.set(job.id,job);prefs.current_job_id=job.id;render();
        }""")
        expect(mobile.locator(".job .badge")).to_have_text("处理失败")
        expect(mobile.locator(".model-result")).to_contain_text("回复等待上限 180 秒")
        expect(mobile.locator(".model-result details")).to_have_attribute("open", "")
        mobile.screenshot(path=str(runtime / "mobile-timeout-diagnostics.png"))
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
