from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

EVIDENCE = Path("evidence/recovery-desk")
HTML = EVIDENCE / "recovery-desk.html"


def main() -> int:
    html_text = HTML.read_text("utf-8")
    if "http://" in html_text or "https://" in html_text:
        raise AssertionError("recovery desk must remain self-contained and offline")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

        # Exercise the exact generated self-contained HTML string. No loopback server or network
        # permission is required, but the real DOM, CSS, responsive realization, and interaction
        # script are still executed by Chromium.
        page.set_content(html_text, wait_until="load")
        if page.locator("body").get_attribute("data-recovery-status") != "READY_ROLLBACK_LAST_GOOD":
            raise AssertionError("wrong recovery status rendered")
        if page.locator("#status-title").inner_text() != "ROLLBACK EVIDENCE READY":
            raise AssertionError("human status title missing")
        if page.locator(".target-card").count() != 2:
            raise AssertionError("both transaction targets must remain visible")
        states = page.locator(".target-card").evaluate_all(
            "els => els.map(el => el.getAttribute('data-target-state'))"
        )
        if states != ["MATCHES_NEW", "MATCHES_LAST_GOOD"]:
            raise AssertionError(f"unexpected target states: {states}")
        if page.evaluate("document.documentElement.scrollWidth") != 390:
            raise AssertionError("mobile recovery desk has horizontal overflow")
        button_box = page.locator("#copy-command").bounding_box()
        if not button_box or button_box["height"] < 44 or button_box["width"] < 44:
            raise AssertionError(f"recovery action target below 44px: {button_box}")
        command = page.locator("#recovery-command").inner_text()
        if "discovery_buddy recover" not in command or "--output-dir" not in command:
            raise AssertionError("explicit production recovery command is not legible")

        page.locator("#copy-command").click()
        page.wait_for_timeout(100)
        copy_feedback = page.locator("#copy-status").inner_text().strip()
        if not copy_feedback:
            raise AssertionError("copy action produced no perceptual feedback")
        page.screenshot(path=str(EVIDENCE / "recovery-desk-mobile.png"), full_page=True)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_timeout(50)
        if page.evaluate("document.documentElement.scrollWidth") != 1280:
            raise AssertionError("desktop recovery desk has horizontal overflow")
        cards = page.locator(".target-card")
        first = cards.nth(0).bounding_box()
        second = cards.nth(1).bounding_box()
        if not first or not second or second["x"] <= first["x"]:
            raise AssertionError("desktop realization did not preserve the two-target comparison")
        page.screenshot(path=str(EVIDENCE / "recovery-desk-desktop.png"), full_page=True)

        browser.close()

    if page_errors or console_errors:
        raise AssertionError(json.dumps({"page_errors": page_errors, "console_errors": console_errors}, indent=2))
    receipt = json.loads((EVIDENCE / "evidence-receipt.json").read_text("utf-8"))
    receipt["browser"] = {
        "mobile_viewport": [390, 844],
        "mobile_horizontal_overflow_px": 0,
        "minimum_recovery_target_px": round(button_box["height"], 2),
        "copy_feedback": copy_feedback,
        "desktop_viewport": [1280, 900],
        "desktop_horizontal_overflow_px": 0,
        "page_errors": 0,
        "console_errors": 0,
        "screenshots": ["recovery-desk-mobile.png", "recovery-desk-desktop.png"],
    }
    (EVIDENCE / "evidence-receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", "utf-8")
    print(json.dumps(receipt["browser"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
