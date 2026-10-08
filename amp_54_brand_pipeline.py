#!/usr/bin/env python3
"""
AMP 54-Brand Pipeline
=======================
Google search each brand → top 10 organic results → Google AMP Test each URL
→ save JSON + screenshots → master all_results.json / .csv / summary.txt

Reuses existing logic from:
  - search_and_scrape.py  (Google search, captcha handling, result extraction)
  - scrape_links.py       (AMP test, wait_for_amp_completion, screenshots)

First runs a TEST with A200M only. Run again without --test to process all 54.
"""

import asyncio
import csv
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_DIR = Path(r"C:\Users\AI Fiham\Pictures\Playwright")
BRANDS_FILE = PROJECT_DIR / "brand.json"
OUTPUT_DIR = Path(r"C:\Users\AI Fiham\Pictures\Playwright\amp_54_brand_run")

# ── Configuration ──────────────────────────────────────────────────────
PARALLEL_WORKERS = 3
AMP_MAX_RETRIES = 2          # retries for ERROR/TIMEOUT only
SEARCH_MAX_RETRIES = 3       # retries for failed Google searches
MIN_DELAY_MS = 4000
MAX_DELAY_MS = 9000
AMP_TEST_TIMEOUT_MS = 120000
PAGE_LOAD_TIMEOUT_MS = 45000
SEARCH_ENGINE = "https://www.google.com/search?udm=14&hl=id&gl=id&q="
REAL_CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    r"C:\Users\AI Fiham\AppData\Local\Google\Chrome\Application\chrome.exe",
)
CHROME_USER_DATA_DIR = os.environ.get(
    "CHROME_USER_DATA",
    r"C:\Users\AI Fiham\AppData\Local\Google\Chrome\User Data",
)
CHROME_PROFILE = os.environ.get("CHROME_PROFILE", "Profile 2")

# ── Ensure rebrowser patch BEFORE import ───────────────────────────────
if "REBROWSER_PATCHES_RUNTIME_FIX_MODE" not in os.environ:
    os.environ["REBROWSER_PATCHES_RUNTIME_FIX_MODE"] = "addBinding"

# ── Ensure UTF-8 console ───────────────────────────────────────────────
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from rebrowser_playwright.async_api import async_playwright, Page, BrowserContext

# Import existing helpers from project scripts
sys.path.insert(0, str(PROJECT_DIR))
from scrape_links import (
    simulate_mouse_movement,
    human_delay,
    human_mouse_move,
    human_scroll,
    child_like_mouse_play,
    child_like_human_type,
    random_delay,
    safe_filename,
    warmup_browsing,
    clear_cookies_and_cache,
    wait_for_amp_completion,
    click_screenshot_tab,
    amp_test_and_save,
    launch_browser,
    STRIP_PLAYWRIGHT_ARTIFACTS,
    prepare_profile_copy,
)
from search_and_scrape import (
    PlaywrightRecaptchaSolver,
    resolve_redirect_url,
    human_type,
    SEARCH_ENGINE as SEARCH_ENGINE_SRC,
)

# ── Helpers ────────────────────────────────────────────────────────────

def safe_name(value: Any) -> str:
    """Filename-safe version of a title."""
    result = re.sub(r"[^a-z0-9]+", "_", str(value or "link"), flags=re.IGNORECASE)
    result = re.sub(r"^_|_$", "", result)
    return result[:80] or "link"


def random_between(min_val: int, max_val: int) -> int:
    return random.randint(min_val, max_val)


async def human_delay_ms(min_ms: float = 100, max_ms: float = 500) -> None:
    delay = random.uniform(min_ms, max_ms)
    await asyncio.sleep(delay / 1000.0)


def brand_output_dir(brand: str) -> Path:
    return OUTPUT_DIR / safe_filename(brand)


# ── Google Search (reuses search_and_scrape.py logic) ──────────────────

async def google_search_brand(
    page: Page,
    brand: str,
    search_index: int,
    total_brands: int,
) -> Optional[List[Dict[str, Any]]]:
    """
    Search Google for one brand. Returns list of organic results (max 10)
    or None if all retries exhausted.

    Each result: {rank, title, url, google_result_url}
    """
    solver = PlaywrightRecaptchaSolver(page)
    last_error = None

    for attempt in range(1, SEARCH_MAX_RETRIES + 1):
        print(f"\n  [Attempt {attempt}/{SEARCH_MAX_RETRIES}] Searching: {brand}")

        # If this is attempt > 1, open a fresh tab
        if attempt > 1:
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
            page = await page.context.new_page()
            await page.context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)
            print(f"    [Search] Fresh tab opened for retry")

        try:
            # 1. Navigate to Google homepage
            await page.goto(
                "https://www.google.com", wait_until="domcontentloaded", timeout=45000
            )
            await page.wait_for_timeout(random_between(1800, 3200))

            # Check for initial captcha
            if await solver.is_captcha_present():
                print(f"    [Captcha] Detected on Google home — solving...")
                solved = await solver.solve(6)
                if not solved:
                    print(f"    [Captcha] Could not solve, retrying...")
                    await page.wait_for_timeout(3000)
                    continue

            # Human-like mouse movement
            await page.mouse.move(
                random_between(200, 900), random_between(150, 450), steps=5
            )
            await page.wait_for_timeout(random_between(400, 900))
            await simulate_mouse_movement(page)

            # 2. Navigate to search results
            search_url = f"{SEARCH_ENGINE}{brand}"
            await page.goto(
                search_url, wait_until="domcontentloaded", timeout=45000
            )
            await page.wait_for_timeout(random_between(2500, 4500))

            # 3. Captcha check on search results
            if await solver.is_captcha_present():
                print(f"    [Captcha] Detected on search results — solving...")
                solved = await solver.solve(6)
                if not solved:
                    print(f"    [Captcha] Could not solve, retrying...")
                    await page.wait_for_timeout(3000)
                    continue
                await page.wait_for_timeout(2000)
                await page.wait_for_load_state("domcontentloaded")

            # Verify not blocked
            if await solver.is_captcha_present() or "sorry/index" in page.url:
                print(f"    [Search] Still blocked, retrying...")
                await page.wait_for_timeout(3000)
                continue

            # 4. Human-like scrolling
            await page.evaluate(
                "() => { window.scrollBy(0, Math.floor(Math.random() * 600) + 200); }"
            )
            await page.wait_for_timeout(random_between(600, 1500))

            # 5. Extract results
            results_locator = page.locator("a h3")
            count = await results_locator.count()

            if count == 0:
                alt = page.locator("#rso h3, #search h3, div.g h3, .MjjYud h3")
                count = await alt.count()

            print(f"    [Search] {brand}: Found {count} result headings")

            if count == 0 and (await solver.is_detected() or "sorry" in page.url):
                print(f"    [Search] Bot detection triggered")
                last_error = "Bot detection"
                await page.wait_for_timeout(3000)
                continue

            # Collect top organic links
            brand_results: List[Dict[str, Any]] = []
            seen_urls: set = set()

            for i in range(min(10, count)):
                try:
                    heading = results_locator.nth(i)
                    title = (await heading.inner_text()).strip()
                    link = await heading.locator("xpath=..").get_attribute("href")

                    if not link:
                        link = await heading.evaluate(
                            "el => el.closest('a')?.getAttribute('href') || null"
                        )

                    if link and link.startswith("/"):
                        link = f"https://www.google.com{link}"

                    # Resolve redirect
                    final_url = await resolve_redirect_url(link, page)

                    if title and final_url and final_url not in seen_urls:
                        seen_urls.add(final_url)
                        print(f"    [{len(brand_results) + 1}] {title}")
                        print(f"         → {final_url}")
                        brand_results.append({
                            "rank": len(brand_results) + 1,
                            "title": title,
                            "url": final_url,
                            "google_result_url": link or final_url,
                        })
                except Exception:
                    continue

            if brand_results:
                print(f"    [Search] ✅ {brand}: {len(brand_results)} organic results")
                return brand_results
            else:
                print(f"    [Search] No results extracted, retrying...")
                last_error = "No results"
                await page.wait_for_timeout(3000)
                continue

        except Exception as e:
            last_error = str(e)
            print(f"    [Search] Error: {e}")
            await page.wait_for_timeout(3000)
            continue

    print(f"    [Search] ❌ {brand}: FAILED after {SEARCH_MAX_RETRIES} attempts: {last_error}")
    return None


# ── AMP Test Wrapper (reuses scrape_links.py logic) ────────────────────

async def run_amp_test(
    page: Page,
    url: str,
    brand: str,
    rank: int,
    title: str,
    brand_dir: Path,
) -> Dict[str, Any]:
    """
    Run Google AMP Test for one URL.
    Returns dict with amp_status, amp_tested_at, screenshot paths.
    """
    safe_title = safe_filename(title) if title else f"rank{rank}"
    base_name = f"{str(rank).zfill(3)}_{safe_title}"
    base = brand_dir / base_name

    result: Dict[str, Any] = {
        "brand": brand,
        "rank": rank,
        "title": title,
        "google_result_url": url,
        "final_url": url,
        "amp_tested_at": None,
        "amp_status": "ERROR",
        "search_screenshot": None,
        "amp_screenshot": None,
        "tested_page_screenshot": None,
    }

    try:
        print(f"    [AMP] Testing rank {rank}: {url[:80]}...")

        # Run the AMP test using existing logic
        amp_status = await amp_test_and_save(page, url, base)

        result["amp_status"] = amp_status
        result["amp_tested_at"] = datetime.now(timezone.utc).isoformat()
        result["amp_screenshot"] = str(base) + "_amp_test.png"

        if amp_status == "PASS":
            tested_page_path = Path(str(base) + "_tested_page.png")
            if tested_page_path.exists():
                result["tested_page_screenshot"] = str(tested_page_path)

        print(f"    [AMP] Rank {rank}: {amp_status}")

    except Exception as e:
        result["amp_status"] = "ERROR"
        result["amp_tested_at"] = datetime.now(timezone.utc).isoformat()
        print(f"    [AMP] Rank {rank}: ERROR — {e}")

    return result


# ── Save Incremental Results ───────────────────────────────────────────

def save_result_json(result: Dict[str, Any], brand_dir: Path) -> None:
    """Save individual result JSON."""
    safe_title = safe_filename(result.get("title") or f"rank{result.get('rank', 0)}")
    filename = f"{str(result['rank']).zfill(3)}_{safe_title}.json"
    path = brand_dir / filename
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def update_master_files(amp_dir: Path) -> None:
    """Rebuild all_results.json and all_results.csv from saved JSON files."""
    all_results: List[Dict[str, Any]] = []

    # Collect all result JSONs
    if amp_dir.exists():
        for brand_dir in sorted(amp_dir.iterdir()):
            if not brand_dir.is_dir():
                continue
            for jf in sorted(brand_dir.glob("*.json")):
                if jf.name.startswith("all_") or jf.name == "summary.txt":
                    continue
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "brand" in data:
                        all_results.append(data)
                except Exception:
                    continue

    # Write all_results.json
    master_json = amp_dir / "all_results.json"
    master_json.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Write all_results.csv
    csv_path = amp_dir / "all_results.csv"
    if all_results:
        fieldnames = [
            "brand", "rank", "title", "google_result_url", "final_url",
            "amp_status", "landing_status", "searched_at", "amp_tested_at",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in all_results:
                writer.writerow(row)
    else:
        csv_path.write_text("", encoding="utf-8")


def update_summary(amp_dir: Path) -> str:
    """Generate and save summary.txt. Returns the summary text."""
    all_results: List[Dict[str, Any]] = []

    if amp_dir.exists():
        for brand_dir in sorted(amp_dir.iterdir()):
            if not brand_dir.is_dir():
                continue
            for jf in sorted(brand_dir.glob("*.json")):
                if jf.name.startswith("all_") or jf.name == "summary.txt":
                    continue
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "brand" in data:
                        all_results.append(data)
                except Exception:
                    continue

    total_brands = len(set(r.get("brand", "") for r in all_results))
    total_results = len(all_results)

    amp_counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "TIMEOUT": 0}
    brand_stats: Dict[str, Dict[str, int]] = {}

    for r in all_results:
        brand = r.get("brand", "Unknown")
        status = r.get("amp_status", "ERROR")
        if brand not in brand_stats:
            brand_stats[brand] = {"RESULTS": 0, "PASS": 0, "FAIL": 0, "ERROR": 0, "TIMEOUT": 0}
        brand_stats[brand]["RESULTS"] += 1
        if status in amp_counts:
            amp_counts[status] += 1
            brand_stats[brand][status] += 1

    total_amp_tests = sum(amp_counts.values())

    lines = []
    lines.append("=" * 70)
    lines.append("AMP 54-BRAND PIPELINE — SUMMARY")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 70)
    lines.append("")
    lines.append("OVERALL STATS")
    lines.append("-" * 40)
    lines.append(f"Brands expected:    54")
    lines.append(f"Brands completed:   {total_brands}")
    lines.append(f"Results collected:  {total_results}")
    lines.append(f"AMP tests completed: {total_amp_tests}")
    lines.append("")
    lines.append("AMP STATUS BREAKDOWN")
    lines.append("-" * 40)

    if total_amp_tests > 0:
        for status in ["PASS", "FAIL", "ERROR", "TIMEOUT"]:
            count = amp_counts[status]
            pct = (count / total_amp_tests * 100) if total_amp_tests > 0 else 0
            lines.append(f"  {status:10s}: {count:5d}  ({pct:5.1f}%)")
    else:
        for status in ["PASS", "FAIL", "ERROR", "TIMEOUT"]:
            lines.append(f"  {status:10s}: {0:5d}")

    lines.append("")
    lines.append("PER-BRAND TABLE")
    lines.append("-" * 70)
    lines.append(f"{'BRAND':20s} {'RESULTS':>8s} {'PASS':>6s} {'FAIL':>6s} {'ERROR':>6s} {'TIMEOUT':>8s}")
    lines.append("-" * 70)

    # Sort brands by name
    for brand in sorted(brand_stats.keys()):
        stats = brand_stats[brand]
        lines.append(
            f"{brand:20s} {stats['RESULTS']:8d} {stats['PASS']:6d} "
            f"{stats['FAIL']:6d} {stats['ERROR']:6d} {stats['TIMEOUT']:8d}"
        )

    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 40)

    brands_fewer = [b for b, s in brand_stats.items() if s["RESULTS"] < 10]
    brands_failed = []
    brands_amp_errors = []
    brands_amp_timeout = []

    for r in all_results:
        brand = r.get("brand", "")
        status = r.get("amp_status", "")
        if status == "ERROR":
            brands_amp_errors.append(brand)
        elif status == "TIMEOUT":
            brands_amp_timeout.append(brand)

    if brands_fewer:
        lines.append(f"Brands with <10 results: {', '.join(sorted(set(brands_fewer)))}")
    if brands_failed:
        lines.append(f"Brands with search failures: {', '.join(sorted(set(brands_failed)))}")
    if brands_amp_errors:
        lines.append(f"Brands with AMP errors: {', '.join(sorted(set(brands_amp_errors)))}")
    if brands_amp_timeout:
        lines.append(f"Brands with AMP timeouts: {', '.join(sorted(set(brands_amp_timeout)))}")

    if not brands_fewer and not brands_amp_errors and not brands_amp_timeout:
        lines.append("No issues detected.")

    lines.append("")
    lines.append("=" * 70)

    summary_text = "\n".join(lines)
    summary_path = amp_dir / "summary.txt"
    summary_path.write_text(summary_text, encoding="utf-8")
    return summary_text


# ── Check Resume Status ────────────────────────────────────────────────

def get_resume_info(amp_dir: Path, brands: List[str]) -> Dict[str, Dict[int, bool]]:
    """
    Returns {brand: {rank: completed}} for already-saved results.
    completed = True means the result JSON exists with a final amp_status.
    """
    resume: Dict[str, Dict[int, bool]] = {}
    for brand in brands:
        resume[brand] = {}
        brand_dir = amp_dir / safe_filename(brand)
        if brand_dir.exists():
            for jf in sorted(brand_dir.glob("*.json")):
                if jf.name.startswith("all_") or jf.name == "summary.txt":
                    continue
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "brand" in data:
                        rank = data.get("rank")
                        status = data.get("amp_status")
                        if rank is not None and status in ("PASS", "FAIL", "ERROR", "TIMEOUT"):
                            resume[brand][rank] = True
                except Exception:
                    continue
    return resume


# ── Process One Brand ──────────────────────────────────────────────────

async def process_brand(
    brand: str,
    page: Page,
    context: BrowserContext,
    brand_index: int,
    total_brands: int,
    resume: Dict[str, Dict[int, bool]],
    test_mode: bool = False,
) -> Dict[str, Any]:
    """
    Process ONE brand: Google search → extract top 10 → AMP test each.
    Returns summary dict for this brand.
    """
    print(f"\n{'='*60}")
    print(f"[{brand_index}/{total_brands}] BRAND: {brand}")
    print(f"{'='*60}")

    brand_dir = brand_output_dir(brand)
    brand_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Google Search ──────────────────────────────────────────
    search_results = await google_search_brand(page, brand, brand_index, total_brands)

    if search_results is None:
        # All retries failed
        print(f"\n  ❌ {brand}: SEARCH FAILED — recording failure")
        fail_result = {
            "brand": brand,
            "rank": 0,
            "title": "SEARCH_FAILED",
            "google_result_url": None,
            "final_url": None,
            "search_query": brand,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "landing_status": "search_failed",
            "amp_status": "ERROR",
            "amp_tested_at": None,
            "search_screenshot": None,
            "amp_screenshot": None,
            "tested_page_screenshot": None,
        }
        save_result_json(fail_result, brand_dir)
        update_master_files(OUTPUT_DIR)
        update_summary(OUTPUT_DIR)
        print(f"\n  {brand} COMPLETE (SEARCH FAILED)")
        print(f"  Results: 0")
        return {
            "brand": brand,
            "status": "search_failed",
            "results_count": 0,
            "amp_counts": {"PASS": 0, "FAIL": 0, "ERROR": 1, "TIMEOUT": 0},
        }

    # Save search screenshot
    search_shot = brand_dir / "search_results.png"
    try:
        await page.screenshot(path=str(search_shot), full_page=True, timeout=30000)
        print(f"    [Screenshot] Search results saved → {search_shot}")
    except Exception as e:
        print(f"    [Screenshot] Search screenshot notice: {e}")

    print(f"\n  Google results: {len(search_results)}")

    # ── Step 2: AMP Test Each Result ──────────────────────────────────
    amp_counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "TIMEOUT": 0}
    completed = 0

    for i, result in enumerate(search_results):
        rank = result["rank"]
        title = result["title"]
        url = result["url"]

        # Check resume: skip if already completed
        if resume.get(brand, {}).get(rank, False):
            print(f"\n  [{i+1}/{len(search_results)}] Rank {rank}: SKIP (already completed)")
            amp_counts[result.get("amp_status", "ERROR")] = amp_counts.get(
                result.get("amp_status", "ERROR"), 0
            )
            # Reload the existing result to get accurate counts
            jf = brand_dir / f"{str(rank).zfill(3)}_{safe_filename(title)}.json"
            if jf.exists():
                try:
                    existing = json.loads(jf.read_text(encoding="utf-8"))
                    st = existing.get("amp_status", "ERROR")
                    amp_counts[st] = amp_counts.get(st, 0) + 1
                except Exception:
                    pass
            completed += 1
            continue

        print(f"\n  [{i+1}/{len(search_results)}] Rank {rank}: {title[:60]}")

        # Set the URL in the result for saving
        result["search_query"] = brand
        result["searched_at"] = datetime.now(timezone.utc).isoformat()
        result["search_screenshot"] = str(search_shot)

        # AMP test with retries for ERROR/TIMEOUT
        amp_result = None
        last_amp_status = "ERROR"

        for amp_attempt in range(AMP_MAX_RETRIES + 1):
            amp_result = await run_amp_test(
                page, url, brand, rank, title, brand_dir
            )
            last_amp_status = amp_result["amp_status"]

            # Merge the AMP result into our result dict
            result["amp_status"] = amp_result["amp_status"]
            result["amp_tested_at"] = amp_result["amp_tested_at"]
            result["amp_screenshot"] = amp_result["amp_screenshot"]
            result["tested_page_screenshot"] = amp_result.get("tested_page_screenshot")

            if last_amp_status in ("PASS", "FAIL"):
                # No retry needed for PASS/FAIL
                break
            elif amp_attempt < AMP_MAX_RETRIES:
                print(f"    [AMP] Retry {amp_attempt + 1}/{AMP_MAX_RETRIES} for ERROR/TIMEOUT...")
                await page.wait_for_timeout(5000)
                # Fresh tab for retry
                try:
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass
                page = await context.new_page()
                await page.context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)

        # Save result JSON
        save_result_json(result, brand_dir)

        # Update master files after EVERY result
        update_master_files(OUTPUT_DIR)
        update_summary(OUTPUT_DIR)

        amp_counts[last_amp_status] = amp_counts.get(last_amp_status, 0) + 1
        completed += 1

        # Live output
        print(f"  [{i+1}/{len(search_results)}] {last_amp_status}")

        # Small delay between AMP tests
        if i < len(search_results) - 1:
            delay = random_delay()
            await asyncio.sleep(delay / 1000.0)

    # ── Brand Complete ────────────────────────────────────────────────
    print(f"\n  {brand} COMPLETE")
    print(f"  Results: {len(search_results)}")
    print(f"  PASS: {amp_counts['PASS']}")
    print(f"  FAIL: {amp_counts['FAIL']}")
    print(f"  ERROR: {amp_counts['ERROR']}")
    print(f"  TIMEOUT: {amp_counts['TIMEOUT']}")

    return {
        "brand": brand,
        "status": "completed",
        "results_count": len(search_results),
        "amp_counts": amp_counts,
    }


# ── Main Pipeline ──────────────────────────────────────────────────────

async def main():
    # ── Load brands ────────────────────────────────────────────────────
    if not BRANDS_FILE.exists():
        print(f"[!] Brands file not found: {BRANDS_FILE}")
        sys.exit(1)

    brands_data = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))
    brands = [str(b).strip() for b in brands_data if str(b).strip()]

    print(f"\n{'='*60}")
    print(f"AMP 54-BRAND PIPELINE")
    print(f"{'='*60}")
    print(f"TOTAL BRANDS FOUND: {len(brands)}")
    print(f"Brands: {', '.join(brands[:5])}... (and {len(brands)-5} more)")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Parallel workers: {PARALLEL_WORKERS}")
    print(f"AMP max retries: {AMP_MAX_RETRIES}")
    print(f"Search max retries: {SEARCH_MAX_RETRIES}")
    print(f"{'='*60}\n")

    if len(brands) != 54:
        print(f"[!] WARNING: Expected 54 brands, found {len(brands)}")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != "y":
            sys.exit(0)

    # ── Determine test mode ────────────────────────────────────────────
    test_mode = "--test" in sys.argv or "-t" in sys.argv
    if test_mode:
        print("[TEST MODE] Running ONLY A200M first...")
        brands_to_run = [b for b in brands if b == "A200M"]
        if not brands_to_run:
            print("[TEST MODE] A200M not found in brand list!")
            sys.exit(1)
    else:
        brands_to_run = brands

    # ── Create output directory ────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Resume check ───────────────────────────────────────────────────
    resume = get_resume_info(OUTPUT_DIR, brands_to_run)
    total_to_resume = sum(
        len(Completed_ranks) for Completed_ranks in resume.values()
    )
    if total_to_resume > 0:
        print(f"[RESUME] Found {total_to_resume} already-completed results")
        print(f"  Skipping completed results, resuming from missing ones...")

    # ── Launch browser ─────────────────────────────────────────────────
    print("\n[Browser] Launching...")
    playwright, context, initial_page = await launch_browser()

    try:
        # Warmup
        await warmup_browsing(initial_page)

        page = initial_page

        # ── Process brands with controlled parallelism ─────────────────
        # Use a semaphore to limit concurrency
        semaphore = asyncio.Semaphore(PARALLEL_WORKERS)

        async def process_with_semaphore(
            brand: str, index: int, total: int
        ) -> Dict[str, Any]:
            async with semaphore:
                return await process_brand(
                    brand, page, context, index, total, resume, test_mode
                )

        tasks = [
            asyncio.create_task(
                process_with_semaphore(brand, i + 1, len(brands_to_run))
            )
            for i, brand in enumerate(brands_to_run)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── Handle exceptions ──────────────────────────────────────────
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                brand = brands_to_run[i]
                print(f"\n[!] [{i+1}/{len(brands_to_run)}] {brand}: EXCEPTION — {result}")

    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass

    # ── Final Summary ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print(f"{'='*60}")

    summary = update_summary(OUTPUT_DIR)
    print(summary)

    print(f"\n✅ Pipeline complete. Files saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
    except Exception as err:
        print(f"[!] Fatal error: {err}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
