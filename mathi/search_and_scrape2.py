#!/usr/bin/env python3
# before running the script install python dependencies and use the following command:
# pip install rebrowser-playwright
# python search_and_scrape.py

import asyncio
import json
import math
import os
import random
import re
import sys
import urllib.parse

# Ensure proper utf-8 console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from stealth_template import (
    create_stealth_browser,
    createStealthBrowser,
    human_type,
    humanType,
    simulate_mouse_movement,
    simulateMouseMovement,
)

# ---------- config ----------
TERMS_FILE = './brand.json'
OUTPUT_DIR = './output'
SEARCH_ENGINE = 'https://www.google.com/search?q='  # change if needed
MIN_DELAY_MS = 4000       # between searches on each tab
MAX_DELAY_MS = 9000
TYPING_DELAY = 80         # ms per key (humanType will vary it)
CONCURRENT_TABS = 54       # run 5 tabs in parallel
TAB_OPEN_DELAY_SEC = 1.0  # 2 seconds gap between opening each tab


# ---------- helpers ----------
def random_delay(min_ms: int = MIN_DELAY_MS, max_ms: int = MAX_DELAY_MS) -> int:
    return math.floor(random.random() * (max_ms - min_ms + 1)) + min_ms


randomDelay = random_delay


async def ensure_dir(dir_path: str) -> None:
    os.makedirs(dir_path, exist_ok=True)


ensureDir = ensure_dir


async def save_results(term: str, page, index: int) -> None:
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', term)[:60]
    base = os.path.join(OUTPUT_DIR, f"{str(index).zfill(3)}_{safe_name}")

    # Screenshot
    await page.screenshot(
        path=f"{base}.png",
        full_page=True,
    )

    # Text content (readable)
    text = await page.evaluate("() => document.body.innerText")
    with open(f"{base}.txt", "w", encoding="utf-8") as f:
        f.write(text or "")

    # Full HTML (for later parsing)
    html = await page.content()
    with open(f"{base}.html", "w", encoding="utf-8") as f:
        f.write(html or "")

    print(f"  → saved {base}.{{png,txt,html}}")


saveResults = save_results


# ---------- tab worker ----------
async def tab_worker(
    tab_id: int,
    context,
    initial_page,
    queue: asyncio.Queue,
    total_terms: int,
) -> None:
    # Stagger: each tab opens with a 2-second gap
    if tab_id > 1:
        await asyncio.sleep((tab_id - 1) * TAB_OPEN_DELAY_SEC)

    if queue.empty():
        return

    # Use initial_page for Tab 1, open new tab for Tab 2, 3, etc.
    if tab_id == 1 and initial_page is not None:
        page = initial_page
    else:
        page = await context.new_page()

    print(f"[Tab {tab_id}] Tab opened and ready.")

    try:
        while not queue.empty():
            try:
                index, term = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                print(f'\n[Tab {tab_id}] [{index}/{total_terms}] Searching: "{term}"')

                # Go to search engine
                await page.goto(
                    SEARCH_ENGINE + urllib.parse.quote(term),
                    wait_until="domcontentloaded",
                    timeout=45000,
                )

                # Human-like mouse movement
                await simulate_mouse_movement(page)

                # Extra short random pause (looks more natural)
                await page.wait_for_timeout(random_delay(800, 2200))

                # Wait for results to settle
                await page.wait_for_selector("body", timeout=15000)

                # Optional: scroll a bit like a human
                await page.evaluate("""() => {
                    window.scrollBy(0, Math.floor(Math.random() * 600) + 200);
                }""")
                await page.wait_for_timeout(random_delay(600, 1500))

                # Scrape + screenshot
                await save_results(term, page, index)

            except Exception as e:
                print(f"  [Tab {tab_id}] Error while processing '{term}': {e}")
            finally:
                queue.task_done()

            # Random delay before next search on this tab
            if not queue.empty():
                delay = random_delay()
                print(f"  [Tab {tab_id}] waiting {round(delay / 1000)}s…")
                await page.wait_for_timeout(delay)

    finally:
        try:
            await page.close()
        except Exception:
            pass


tabWorker = tab_worker


# ---------- main ----------
async def main() -> None:
    with open(TERMS_FILE, "r", encoding="utf-8") as f:
        terms = json.load(f)

    if not isinstance(terms, list) or len(terms) == 0:
        raise ValueError("terms.json must be a non-empty array of strings")

    await ensure_dir(OUTPUT_DIR)

    # Populate search queue
    queue: asyncio.Queue = asyncio.Queue()
    for i, raw_term in enumerate(terms):
        term = str(raw_term).strip()
        if term:
            queue.put_nowait((i + 1, term))

    if queue.empty():
        print("No valid terms to search.")
        return

    total_terms = queue.qsize()
    num_tabs = min(CONCURRENT_TABS, total_terms)

    print(f"Starting parallel search: {total_terms} terms across {num_tabs} tabs (staggered by {TAB_OPEN_DELAY_SEC}s gap)...")

    session = await create_stealth_browser({
        # optional overrides if the helper accepts them
        # headless: False is forced by the skill
    })
    browser = session.browser
    context = session.context
    initial_page = session.page

    try:
        tasks = [
            asyncio.create_task(
                tab_worker(
                    tab_id=t + 1,
                    context=context,
                    initial_page=(initial_page if t == 0 else None),
                    queue=queue,
                    total_terms=total_terms,
                )
            )
            for t in range(num_tabs)
        ]

        await asyncio.gather(*tasks)
        print("\nDone. All results in ./output/")
    finally:
        # CRITICAL — always close, otherwise the process hangs
        try:
            await browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as err:
        print(err, file=sys.stderr)
        sys.exit(1)
