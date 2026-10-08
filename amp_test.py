#!/usr/bin/env python3
"""
AMP Test Script
Converted from the original JavaScript version.
"""

import os
import sys
import json
import time
import asyncio
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, quote

# Import from local stealth template
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
try:
    from stealth_template import create_stealth_browser, simulate_mouse_movement
except ImportError:
    from stealth_template import create_stealth_browser, simulate_mouse_movement

from playwright.async_api import Page

INPUT_JSON = os.environ.get("INPUT_JSON", "")
OUTPUT_DIR = os.environ.get("AMP_OUTPUT_DIR", "./amp-output")
MAX_LINKS = int(os.environ.get("MAX_LINKS", "10"))
REAL_CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    r"C:\Users\Thushalika\AppData\Local\Google\Chrome\Application\chrome.exe",
)
LANDING_NAVIGATION_TIMEOUT_MS = 60000
LANDING_SETTLE_MS = 10000
AMP_MAX_WAIT_MS = int(os.environ.get("AMP_MAX_WAIT_MS", "120000"))
AMP_POLL_MS = 2000


def safe_name(value: Any) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", str(value or "link"), flags=re.IGNORECASE)
    result = re.sub(r"^_|_$", "", result)
    return result[:80] or "link"


async def find_input_json() -> str:
    if INPUT_JSON:
        return str(Path(INPUT_JSON).resolve())

    output_root = Path("./output").resolve()
    if not output_root.exists():
        raise FileNotFoundError("Could not find search_results.json. Set INPUT_JSON to its path.")

    cycles = [
        entry.name
        for entry in output_root.iterdir()
        if entry.is_dir() and entry.name.startswith("cycle_")
    ]
    cycles.sort(reverse=True)

    for cycle in cycles:
        candidate = output_root / cycle / "search_results.json"
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError("Could not find search_results.json. Set INPUT_JSON to its path.")


async def load_links(json_path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else [data]
    links: List[Dict[str, Any]] = []

    for record in records:
        brand = str(record.get("brand") or "unknown")
        results = record.get("results") if isinstance(record.get("results"), list) else []
        for result in results[:MAX_LINKS]:
            url = result.get("url") or ""
            if not re.match(r"^https?://", url, re.IGNORECASE):
                continue
            links.append({
                "brand": brand,
                "rank": result.get("rank") or len(links) + 1,
                "title": result.get("title") or "",
                "url": url,
            })

    if not links:
        raise ValueError(f"No HTTP links found in {json_path}")
    return links


async def wait_for_amp_completion(page: Page) -> Dict[str, str]:
    deadline = time.time() * 1000 + AMP_MAX_WAIT_MS

    while (time.time() * 1000) < deadline:
        try:
            body_text = (await page.locator("body").inner_text()).lower()
        except Exception:
            body_text = ""

        valid = bool(re.search(r"valid amp page|amp page is valid|valid amp", body_text))
        invalid = bool(
            re.search(r"not an amp page|not a valid amp|invalid amp|does not use amp", body_text)
        )

        view_tested_page = (
            page.locator(
                '[aria-label*="view tested page" i], [title*="view tested page" i], '
                "a, button, [role='button']"
            )
            .filter(has_text=re.compile(r"view tested page", re.IGNORECASE))
            .first
        )

        try:
            has_view_tested_page = await view_tested_page.is_visible()
        except Exception:
            has_view_tested_page = False

        if valid or invalid or has_view_tested_page:
            status = "valid" if (not invalid and (valid or has_view_tested_page)) else "invalid"
            return {"status": status}

        await page.wait_for_timeout(AMP_POLL_MS)

    return {"status": "timeout"}


async def click_screenshot_tab(page: Page) -> bool:
    screenshot_tab = (
        page.locator('[role="tab"]')
        .filter(has_text=re.compile(r"^SCREENSHOT$", re.IGNORECASE))
        .first
    )
    exact_label = page.get_by_text("SCREENSHOT", exact=True).first

    try:
        is_screenshot_visible = await screenshot_tab.is_visible()
    except Exception:
        is_screenshot_visible = False

    tab = screenshot_tab if is_screenshot_visible else exact_label

    await tab.wait_for(state="visible", timeout=10000)
    await tab.click(timeout=10000)
    await page.wait_for_timeout(3000)

    try:
        selected = await tab.get_attribute("aria-selected")
    except Exception:
        selected = None

    return selected != "false"


async def capture_link(
    page: Page,
    link: Dict[str, Any],
    index: int,
    total: int,
    output_dir: str,
) -> Dict[str, Any]:
    hostname = urlparse(link["url"]).hostname or "unknown"
    prefix = (
        f"{str(index).zfill(3)}_{safe_name(link['brand'])}_"
        f"{str(link['rank']).zfill(2)}_{safe_name(hostname)}"
    )
    landing_path = str(Path(output_dir) / f"{prefix}_landing.png")
    amp_path = str(Path(output_dir) / f"{prefix}_amp-test.png")
    tested_page_path = str(Path(output_dir) / f"{prefix}_tested-page.png")

    result: Dict[str, Any] = {
        **link,
        "landing_screenshot": landing_path,
        "amp_test_screenshot": amp_path,
        "tested_page_screenshot": tested_page_path,
    }

    print(f"[{index}/{total}] Landing page: {link['url']}")
    try:
        await page.goto(
            link["url"],
            wait_until="domcontentloaded",
            timeout=LANDING_NAVIGATION_TIMEOUT_MS,
        )
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=LANDING_NAVIGATION_TIMEOUT_MS
            )
        except Exception:
            print("  Network idle was not reached; continuing after render wait.")

        print(
            f"  Waiting {round(LANDING_SETTLE_MS / 1000)}s for the landing page to finish rendering..."
        )
        await page.wait_for_timeout(LANDING_SETTLE_MS)
        await simulate_mouse_movement(page, 3)
        await page.screenshot(path=landing_path, full_page=True)
        result["landing_url"] = page.url
        result["landing_status"] = "ok"
    except Exception as error:
        result["landing_status"] = "error"
        result["landing_error"] = str(error)
        print(f"  Landing page failed: {error}")

    amp_test_url = f"https://search.google.com/test/amp?url={quote(link['url'], safe='')}"
    print(f"[{index}/{total}] AMP test")
    try:
        await page.goto(
            amp_test_url,
            wait_until="domcontentloaded",
            timeout=AMP_MAX_WAIT_MS,
        )
        print(
            f"  Waiting for AMP completion (maximum {round(AMP_MAX_WAIT_MS / 1000)}s)..."
        )
        amp_result = await wait_for_amp_completion(page)
        await page.screenshot(path=amp_path, full_page=True)
        result["amp_test_url"] = page.url
        result["amp_test_status"] = amp_result["status"]

        if amp_result["status"] == "valid":
            view_tested_page = (
                page.locator(
                    '[aria-label*="view tested page" i], [title*="view tested page" i], '
                    "a, button, [role='button']"
                )
                .filter(has_text=re.compile(r"view tested page", re.IGNORECASE))
                .first
            )

            try:
                is_visible = await view_tested_page.is_visible()
            except Exception:
                is_visible = False

            if is_visible:
                await view_tested_page.click(timeout=10000)
                await page.wait_for_timeout(3000)
                result["view_tested_page_clicked"] = True
                result["screenshot_tab_clicked"] = await click_screenshot_tab(page)
                await page.screenshot(path=tested_page_path, full_page=True)
            else:
                result["view_tested_page_clicked"] = False
                result["screenshot_tab_clicked"] = False
    except Exception as error:
        result["amp_test_status"] = "error"
        result["amp_test_error"] = str(error)
        print(f"  AMP test failed: {error}")

    return result


async def main() -> None:
    input_json = await find_input_json()
    links = await load_links(input_json)

    # Create run directory with ISO-like timestamp
    timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")
    run_dir = Path(OUTPUT_DIR).resolve() / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    executable_path = REAL_CHROME_PATH if Path(REAL_CHROME_PATH).exists() else None

    browser, context, page = await create_stealth_browser(
        headless=False,
        viewport={"width": 1366, "height": 768},
        executable_path=executable_path,
    )

    results: List[Dict[str, Any]] = []
    try:
        for index, link in enumerate(links):
            results.append(
                await capture_link(page, link, index + 1, len(links), str(run_dir))
            )
    finally:
        await browser.close()

    output_data = {
        "input_json": input_json,
        "results": results,
    }
    (run_dir / "amp_results.json").write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Finished. Screenshots and amp_results.json saved to {run_dir}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"[!] Fatal execution error: {error}")
        sys.exit(1)
