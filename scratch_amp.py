import asyncio
import os
import sys
import time
import urllib.parse
from pathlib import Path
from playwright.async_api import async_playwright

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Configuration ──
TARGET_URL = "https://rank.a200m-betting.com/"
WAIT_TIMEOUT_SECONDS = 120  # 2 minutes waiting time for results
SCREENSHOT_PATH = "amp_test_results.png"

STRIP_PLAYWRIGHT_ARTIFACTS = """
() => {
  const hide = (k) => {
    try { delete window[k]; } catch { }
    if (Object.prototype.hasOwnProperty.call(window, k)) {
      try { Object.defineProperty(window, k, { get: () => undefined, configurable: true }); } catch { }
    }
  };
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/^__pw|pwInitScripts|playwright/i.test(k)) hide(k);
  }
  if (!window.chrome) window.chrome = {};
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
}
"""

async def run_amp_test():
    p = await async_playwright().start()

    # Launch browser
    browser = await p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="en-US",
    )
    await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)
    page = await context.new_page()

    amp_url = f"https://search.google.com/test/amp?url={urllib.parse.quote(TARGET_URL, safe='')}"
    print(f"🚀 Opening Google AMP Test: {amp_url}")
    await page.goto(amp_url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(3000)

    # 1. Click "TEST URL" button or press Enter on input
    print("🔍 Submitting test for URL...")
    btn = page.locator("div[role='button']:has-text('TEST URL'), button:has-text('TEST URL'), div[role='button'][jsname='LZQqje']").first

    if await btn.count() > 0 and await btn.is_visible():
        try:
            box = await btn.bounding_box()
            if box:
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await page.wait_for_timeout(200)
                await page.mouse.down()
                await page.wait_for_timeout(100)
                await page.mouse.up()
            else:
                await btn.click(force=True)
            print("  → Clicked 'TEST URL' button")
        except Exception as e:
            print(f"  → Button click failed ({e}), trying Enter key...")
            inp = page.locator("input[type='url'], input[aria-label*='URL' i]").first
            await inp.press("Enter")
    else:
        inp = page.locator("input[type='url'], input[aria-label*='URL' i]").first
        if await inp.count() > 0:
            await inp.press("Enter")
            print("  → Pressed Enter on URL input")

    # 2. Wait up to 2 minutes (120 seconds) for results to load
    print(f"\n⏳ Waiting up to {WAIT_TIMEOUT_SECONDS} seconds (2 minutes) for test results to appear...")
    start_time = time.time()
    result_found = False
    result_status = "UNKNOWN"

    while (time.time() - start_time) < WAIT_TIMEOUT_SECONDS:
        elapsed = int(time.time() - start_time)
        remaining = WAIT_TIMEOUT_SECONDS - elapsed

        # Check page text and URL
        current_url = page.url
        text = await page.evaluate("() => document.body.innerText")
        text_lower = text.lower()

        # Check for error popup
        if "something went wrong" in text_lower or "log in and try again" in text_lower:
            print(f"  [{elapsed}s] ⚠️ Notice: Google prompted login or temporary error.")
            result_status = "ERROR_LOGIN_REQUIRED"
            result_found = True
            break

        # Check for completed results
        is_result_page = (
            "/result" in current_url
            or "tested on" in text_lower
            or "page is valid amp" in text_lower
            or "not a valid amp page" in text_lower
            or "valid amp with warnings" in text_lower
            or "page cannot be reached" in text_lower
            or "crawled successfully" in text_lower
        )

        is_still_testing = (
            "testing live url" in text_lower
            or "analyzing" in text_lower
            or "running test" in text_lower
        )

        if is_result_page and not is_still_testing:
            is_valid = (
                "amp page is valid" in text_lower
                or "page is valid amp" in text_lower
                or "valid amp" in text_lower
            ) and not (
                "not a valid amp" in text_lower
                or "is not valid" in text_lower
                or "page cannot be reached" in text_lower
            )

            if is_valid:
                if "with warnings" in text_lower:
                    result_status = "VALID_AMP_WITH_WARNINGS (PASS)"
                else:
                    result_status = "VALID_AMP (PASS)"
            else:
                result_status = "NOT_VALID_AMP (FAIL)"

            print(f"  [{elapsed}s] ✅ Results loaded successfully! Status: {result_status}")
            result_found = True
            break
        else:
            status_msg = "Testing in progress..." if is_still_testing else "Waiting for results to render..."
            print(f"  [{elapsed}s / {WAIT_TIMEOUT_SECONDS}s] {status_msg} ({remaining}s remaining)")

        await page.wait_for_timeout(3000)

    # 3. Extra settling pause to ensure all charts/details render
    await page.wait_for_timeout(3000)

    # 4. Take the full screenshot
    print(f"\n📸 Taking full-page screenshot of the results...")
    await page.screenshot(path=SCREENSHOT_PATH, full_page=True)
    print(f"✅ Screenshot saved → {Path(SCREENSHOT_PATH).resolve()}")
    print(f"📋 Final AMP Test Status: {result_status}")

    await context.close()
    await browser.close()
    await p.stop()

if __name__ == "__main__":
    asyncio.run(run_amp_test())
