#!/usr/bin/env python3
"""
scrape_links.py
───────────────
Reads every JSON file in results_json/, visits each URL listed in
the "results" array, scrapes the full page (text + HTML + screenshot),
runs a Google AMP Test, and saves everything to link_scrape_output/<brand>/

Uses the same anti-detection / bot-bypass concepts as Final.py:
  - rebrowser-playwright (REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding)
  - Random fingerprint  (User-Agent + viewport rotation each launch)
  - Warmup browsing    (visit random benign sites before starting)
  - Human mouse moves  (Bezier curve + multi-step natural movement)
  - Human scrolling    (random direction & distance)
  - Human typing       (per-character delay when entering URLs)
  - Playwright artifact stripping init script
  - Google login check before starting AMP tests

Requirements:
    pip install rebrowser-playwright
    playwright install chrome
"""

import asyncio
import json
import math
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

# ── rebrowser Runtime.enable fix MUST be set BEFORE importing the library ──
if "REBROWSER_PATCHES_RUNTIME_FIX_MODE" not in os.environ:
    os.environ["REBROWSER_PATCHES_RUNTIME_FIX_MODE"] = "addBinding"

# ── Ensure UTF-8 console on Windows ──
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from rebrowser_playwright.async_api import async_playwright, Page, BrowserContext

# ====================== CONFIGURATION ======================
RESULTS_JSON_DIR  = "./results_json"        # folder containing the brand JSON files
OUTPUT_DIR        = "./link_scrape_output"  # where scraped pages are saved

# ── Chrome real-browser paths (same as your other scripts) ──
REAL_CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    r"C:\Users\AI Fiham\AppData\Local\Google\Chrome\Application\chrome.exe"
)
CHROME_USER_DATA_DIR = os.environ.get(
    "CHROME_USER_DATA",
    r"C:\Users\AI Fiham\AppData\Local\Google\Chrome\User Data"
)
CHROME_PROFILE = os.environ.get("CHROME_PROFILE", "Profile 2")

# ── Parallelism ──
CONCURRENT_TABS    = 1      # how many tabs run at the same time
TAB_OPEN_DELAY_SEC = 2.0    # stagger gap between tab openings (seconds)

# ── Page load timing ──
PAGE_LOAD_TIMEOUT  = 45_000   # ms – max time to wait for a page
MIN_DELAY_MS       = 2_000    # ms – random delay between visits on a tab
MAX_DELAY_MS       = 6_000

# ── Google AMP Test ──
AMP_TEST_TIMEOUT   = 120_000  # ms – 2 minutes waiting time for results

# ── Anti-Detection Settings (mirrored from Final.py) ──
ENABLE_WARMUP = True
WARMUP_SITES = [
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://www.bbc.com",
    "https://edition.cnn.com",
    "https://www.reuters.com",
    "https://news.ycombinator.com",
    "https://www.weather.com",
]

# Pool of realistic Chrome User-Agent strings to rotate (Final.py pattern)
USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

# Pool of viewport sizes to randomize browser fingerprint (Final.py pattern)
VIEWPORT_POOL = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]

# ── Profile files to copy (keeps login cookies alive) ──
PROFILE_FILES_TO_COPY = [
    os.path.join("Network", "Cookies"),
    os.path.join("Network", "Cookies-journal"),
    "Cookies", "Cookies-journal",
    "Extension Cookies", "Extension Cookies-journal",
    "Login Data", "Login Data-journal",
    "Login Data For Account", "Login Data For Account-journal",
    "Preferences", "Secure Preferences",
    "Web Data", "Web Data-journal",
    "Bookmarks", "Favicons",
]

# ── Stealth init script — IIFE form, same as Final.py ──
STRIP_PLAYWRIGHT_ARTIFACTS = """
(() => {
  const hide = (k) => {
    try { delete window[k]; } catch (e) { /* non-configurable */ }
    if (Object.prototype.hasOwnProperty.call(window, k)) {
      try { Object.defineProperty(window, k, { get: () => undefined, configurable: true }); } catch (e) { /* sealed */ }
    }
  };
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/^__pw|pwInitScripts|playwright/i.test(k)) hide(k);
  }
  if (!window.chrome) window.chrome = {};
})()
"""


# ====================== ANTI-DETECTION HELPERS (from Final.py) ======================

def get_random_fingerprint():
    """Generate a randomized browser fingerprint for each session (Final.py pattern)."""
    return {
        "user_agent": random.choice(USER_AGENT_POOL),
        "viewport":   random.choice(VIEWPORT_POOL),
    }


def random_delay(min_ms: int = MIN_DELAY_MS, max_ms: int = MAX_DELAY_MS) -> int:
    """Return a random delay in milliseconds."""
    return math.floor(random.random() * (max_ms - min_ms + 1)) + min_ms


async def human_delay(min_ms: float = 100, max_ms: float = 500) -> None:
    """Add a human-like random delay between actions (Final.py pattern)."""
    delay = random.uniform(min_ms, max_ms)
    await asyncio.sleep(delay / 1000.0)


async def simulate_mouse_movement(page: Page, moves: int = None) -> None:
    """
    Simulate natural mouse movement on the page with steps=10 for smooth
    interpolated trajectories (Final.py / stealth_template2.py pattern).
    """
    try:
        count = moves if moves is not None else (5 + math.floor(random.random() * 5))
        for _ in range(count):
            await page.mouse.move(
                100 + random.random() * 600,
                100 + random.random() * 400,
                steps=10,
            )
            await human_delay(50, 200)
    except Exception:
        pass


async def human_mouse_move(page: Page, target_x: float, target_y: float, steps: int = None) -> None:
    """
    Move the mouse in a natural Bezier curve to target coordinates (Final.py pattern).
    Falls back to a direct move on any error.
    """
    try:
        start_x = random.randint(100, 600)
        start_y = random.randint(100, 400)

        if steps is None:
            distance = math.sqrt((target_x - start_x) ** 2 + (target_y - start_y) ** 2)
            steps = max(8, int(distance / 15))

        cp1_x = start_x + (target_x - start_x) * random.uniform(0.2, 0.5) + random.randint(-80, 80)
        cp1_y = start_y + (target_y - start_y) * random.uniform(0.2, 0.5) + random.randint(-80, 80)
        cp2_x = start_x + (target_x - start_x) * random.uniform(0.5, 0.8) + random.randint(-40, 40)
        cp2_y = start_y + (target_y - start_y) * random.uniform(0.5, 0.8) + random.randint(-40, 40)

        for i in range(steps + 1):
            t = i / steps
            x = (
                (1 - t) ** 3 * start_x
                + 3 * (1 - t) ** 2 * t * cp1_x
                + 3 * (1 - t) * t ** 2 * cp2_x
                + t ** 3 * target_x
            )
            y = (
                (1 - t) ** 3 * start_y
                + 3 * (1 - t) ** 2 * t * cp1_y
                + 3 * (1 - t) * t ** 2 * cp2_y
                + t ** 3 * target_y
            )
            await page.mouse.move(x, y)
            # Variable speed — slower at start/end (ease-in-out)
            delay = random.uniform(0.005, 0.025) * (1 + 0.5 * math.sin(math.pi * t))
            await asyncio.sleep(delay)
    except Exception:
        try:
            await page.mouse.move(target_x, target_y)
        except Exception:
            pass


async def human_scroll(page: Page) -> None:
    """Perform random human-like scrolling on the current page (Final.py pattern)."""
    try:
        scroll_actions = random.randint(2, 5)
        for _ in range(scroll_actions):
            direction = random.choice(["down", "down", "down", "up"])
            distance = random.randint(100, 400)
            if direction == "up":
                distance = -distance
            await page.mouse.wheel(0, distance)
            await asyncio.sleep(random.uniform(0.3, 1.2))
    except Exception:
        pass


async def child_like_mouse_play(page: Page, moves: int = 3) -> None:
    """
    Simulate lively, natural, curious mouse exploration across the page:
    smooth curves, slight jitter, pauses, wandering, and random scrolls.
    """
    try:
        viewport = page.viewport_size or {"width": 1366, "height": 768}
        w = max(400, viewport.get("width", 1366))
        h = max(300, viewport.get("height", 768))

        for _ in range(moves):
            tx = random.uniform(80, w - 80)
            ty = random.uniform(80, h - 80)
            await human_mouse_move(page, tx, ty)
            await human_delay(120, 350)

            if random.random() < 0.35:
                scroll_y = random.choice([50, -50, 100, -80])
                await page.mouse.wheel(0, scroll_y)
                await human_delay(100, 250)
    except Exception:
        pass


async def child_like_human_type(page: Page, element, text: str) -> None:
    """
    Type text char-by-char with realistic, human/child-like cadence:
    - Variable per-char typing speed
    - Occasional hesitation/thinking pauses on dots, slashes, or batches of letters
    - Occasional realistic typos (2-3% chance) that get quickly backspaced & corrected
    - Small micro-mouse twitch during long text input
    """
    try:
        box = await element.bounding_box()
        if box:
            overshoot_x = box["x"] + box["width"] / 2 + random.uniform(-15, 15)
            overshoot_y = box["y"] + box["height"] / 2 + random.uniform(-8, 8)
            await human_mouse_move(page, overshoot_x, overshoot_y)
            await human_delay(150, 300)

        await element.click()
        await human_delay(150, 300)

        # Clear existing text
        await page.keyboard.press("Control+A")
        await human_delay(50, 120)
        await page.keyboard.press("Backspace")
        await human_delay(150, 300)

        typo_chars = "abcdefghijklmnopqrstuvwxyz"
        for i, char in enumerate(text):
            # 2.5% chance of realistic typo on alphabetic characters
            if char.isalpha() and random.random() < 0.025:
                wrong_char = random.choice(typo_chars)
                await page.keyboard.type(wrong_char)
                await human_delay(120, 280)
                await page.keyboard.press("Backspace")
                await human_delay(80, 180)

            await page.keyboard.type(char)

            # Random per-character delay
            delay = random.uniform(40, 140)

            # Extra thinking pause at punctuation or periodically
            if char in [".", "/", ":", "-", "_", "?"]:
                delay += random.uniform(150, 450)
            elif i > 0 and (i % random.randint(7, 12) == 0):
                delay += random.uniform(120, 350)
                if random.random() < 0.2:
                    await page.mouse.move(
                        random.randint(200, 600),
                        random.randint(200, 500),
                        steps=3
                    )

            await asyncio.sleep(delay / 1000.0)

        await human_delay(200, 500)
    except Exception as e:
        print(f"    [Type] Fallback to direct fill: {e}")
        try:
            await element.fill(text)
        except Exception:
            pass


async def clear_cookies_and_cache(page: Page) -> None:
    """
    Clears all browser cookies, cache, local storage, and session data
    after each AMP test safely without crashing the CDP target.
    """
    try:
        if not page.is_closed():
            await page.context.clear_cookies()
    except Exception:
        pass

    try:
        if not page.is_closed():
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Network.clearBrowserCache")
            await cdp.send("Network.clearBrowserCookies")
            await cdp.detach()
    except Exception:
        pass

    try:
        if not page.is_closed():
            await page.evaluate("""() => {
                try { localStorage.clear(); } catch(e){}
                try { sessionStorage.clear(); } catch(e){}
            }""")
    except Exception:
        pass

    print("    [Cleaner] 🧹 Cleared cookies, cache, and browser data")


async def human_type_into(page: Page, element, text: str) -> None:
    """
    Type text with human-like per-character speed (Final.py / stealth_template2.py pattern).
    Clicks the element first, then types char-by-char.
    """
    await child_like_human_type(page, element, text)


async def warmup_browsing(page: Page) -> None:
    """Visit a few random benign websites to build realistic session history (Final.py pattern)."""
    if not ENABLE_WARMUP:
        return

    sites = random.sample(WARMUP_SITES, k=min(random.randint(1, 3), len(WARMUP_SITES)))
    print(f"\U0001F310 [Warmup] Visiting {len(sites)} random site(s) to build browsing history...")

    for site in sites:
        try:
            await page.goto(site, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(2000, 5000))
            await human_scroll(page)
            await page.wait_for_timeout(random.randint(1000, 3000))
            await human_mouse_move(page, random.randint(200, 800), random.randint(150, 500))
            await simulate_mouse_movement(page)
            print(f"\U0001F310 [Warmup] Visited: {site.split('/')[2]}")
        except Exception as e:
            print(f"\U0001F310 [Warmup] Skipped {site}: {e}")

    try:
        await page.goto("about:blank")
    except Exception:
        pass
    print("\U0001F310 [Warmup] Done.\n")


# ====================== GOOGLE LOGIN CHECK (from Final.py) ======================
async def is_google_logged_in(context: BrowserContext) -> bool:
    """Check if the browser context has active Google authentication cookies."""
    try:
        cookies = await context.cookies(["https://www.google.com", "https://accounts.google.com"])
        return any(c.get("name") in ("SID", "HSID", "SSID") for c in cookies)
    except Exception:
        return False


async def ensure_google_login(context: BrowserContext) -> None:
    """
    Ensure the persistent Chrome profile is logged into a Google/Gmail account.
    Needed so Google AMP Test doesn't redirect to login walls (Final.py pattern).
    """
    if await is_google_logged_in(context):
        print("\U0001F464 [Account] Google/Gmail login detected and active.")
        return

    print("\n" + "=" * 65)
    print("\u26A0\uFE0F [LOGIN REQUIRED] Google account is not logged in.")
    print("Opening Google sign-in page in Chrome...")
    print("\U0001F449 Please sign in to your Gmail/Google account in the browser.")
    print("\U0001F449 Once logged in, the script will automatically detect it and proceed.")
    print("=" * 65 + "\n")

    login_page = context.pages[0] if context.pages else await context.new_page()
    try:
        await login_page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded")
    except Exception as e:
        print(f"[!] Notice navigating to sign-in page: {e}")

    for _ in range(90):
        await asyncio.sleep(2)
        if await is_google_logged_in(context):
            print("\n\u2705 Google login detected! Continuing...\n")
            try:
                await login_page.goto("about:blank")
            except Exception:
                pass
            return

    print("\u26A0\uFE0F Notice: Login not detected within timeout. Continuing with current session...")


# ====================== HELPERS ======================


def safe_filename(text: str, max_len: int = 60) -> str:
    """Turn arbitrary text into a safe filename fragment."""
    return re.sub(r"[^a-zA-Z0-9\-_]", "_", text)[:max_len]


# ====================== PROFILE COPY ======================
async def prepare_profile_copy() -> str:
    src_dir   = Path(CHROME_USER_DATA_DIR) / CHROME_PROFILE
    session_dir = Path("chrome-session").resolve()
    dest_dir  = session_dir / CHROME_PROFILE

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Clean up stale lock files from previous aborted sessions
    for lock_name in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
        for p in [session_dir / lock_name, dest_dir / lock_name]:
            if p.exists():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

    # Copy Local State (encryption keys)
    local_state_src = Path(CHROME_USER_DATA_DIR) / "Local State"
    local_state_dst = session_dir / "Local State"
    if local_state_src.exists():
        try:
            shutil.copy2(local_state_src, local_state_dst)
        except Exception as e:
            print(f"[Profile] Could not copy Local State: {e}")

    copied = 0
    locked = 0
    for file_name in PROFILE_FILES_TO_COPY:
        src = src_dir / file_name
        dst = dest_dir / file_name
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                locked += 1
                if "locked" in str(e).lower() or "used by another process" in str(e).lower():
                    print(f"[Profile] ⚠️ File locked (Chrome is running): {file_name}")
                else:
                    print(f"[Profile] Notice: '{file_name}': {e}")

    print(f"[Profile] Copied {copied} session files for '{CHROME_PROFILE}' → ./chrome-session/")
    if locked > 0:
        print("\n" + "=" * 65)
        print(f"⚠️  [NOTICE] {locked} session file(s) were locked because Chrome is open.")
        print(f"👉 For 100% full login access with {CHROME_PROFILE} (mvannangravity@gmail.com),")
        print("   close Chrome completely and re-run the script.")
        print("=" * 65 + "\n")
    return str(session_dir)


# ====================== BROWSER LAUNCH ======================
async def launch_browser():
    """
    Launch persistent Chrome context with:
    - Copied profile (keeps cookies / login alive)
    - Random fingerprint: User-Agent + viewport (Final.py pattern)
    - Anti-detection args (disable AutomationControlled, --start-maximized)
    - Playwright artifact-stripping IIFE init script
    """
    print(f"[Browser] Preparing profile copy…")
    session_dir = await prepare_profile_copy()

    # Pre-clean any leftover lock files in session directory
    for lock_name in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
        for p in Path(session_dir).rglob(lock_name):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    fingerprint = get_random_fingerprint()
    print(f"🎭 [Stealth] Viewport : {fingerprint['viewport']['width']}x{fingerprint['viewport']['height']}")
    print(f"🎭 [Stealth] UserAgent: {fingerprint['user_agent'][:70]}...")

    executable_path = REAL_CHROME_PATH if Path(REAL_CHROME_PATH).exists() else None
    if executable_path:
        print(f"[Browser] Executable: {executable_path}")
    print(f"[Browser] Session dir: {session_dir}")

    playwright = await async_playwright().start()

    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    if CHROME_PROFILE:
        args.append(f"--profile-directory={CHROME_PROFILE}")

    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=session_dir,
            headless=False,
            args=args,
            viewport=fingerprint["viewport"],
            user_agent=fingerprint["user_agent"],
            ignore_default_args=["--enable-automation"],
            timezone_id="Asia/Jakarta",
            executable_path=executable_path,
        )
    except Exception as launch_err:
        if "ProcessSingleton" in str(launch_err) or "closed pipe" in str(launch_err):
            print(f"[Browser] ⚠️ Detected profile lock ({launch_err}). Cleaning lock files & retrying...")
            for lock_name in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
                for p in Path(session_dir).rglob(lock_name):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
            await asyncio.sleep(1.5)
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=session_dir,
                headless=False,
                args=args,
                viewport=fingerprint["viewport"],
                user_agent=fingerprint["user_agent"],
                ignore_default_args=["--enable-automation"],
                timezone_id="Asia/Jakarta",
                executable_path=executable_path,
            )
        else:
            raise launch_err

    # Inject artifact-stripping IIFE on every page navigation (Final.py pattern)
    await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)

    pages = context.pages
    initial_page = pages[0] if pages else await context.new_page()

    return playwright, context, initial_page


# ====================== GOOGLE AMP TEST (from amp_test.py) ======================
AMP_POLL_MS = 2000


async def wait_for_amp_completion(page: Page, max_wait_ms: int = AMP_TEST_TIMEOUT) -> str:
    """
    Waits up to max_wait_ms for Google AMP test to complete.
    Checks body text, 'view tested page' locator, and handles error popups.
    Returns 'PASS', 'FAIL', 'ERROR', or 'TIMEOUT'.
    """
    deadline = time.time() * 1000 + max_wait_ms

    while (time.time() * 1000) < deadline:
        if page.is_closed():
            return "ERROR"

        try:
            body_text = (await page.locator("body").inner_text()).lower()
        except Exception:
            body_text = ""

        # Check for error popup ("Something went wrong")
        if "something went wrong" in body_text or "try again in a few hours" in body_text:
            print("    [AMP] ⚠️ 'Something went wrong' popup detected. Dismissing...")
            try:
                dismiss_btn = page.locator("button:has-text('Dismiss'), div[role='button']:has-text('Dismiss')").first
                if await dismiss_btn.is_visible():
                    await dismiss_btn.click()
            except Exception:
                pass
            return "ERROR"

        is_still_testing = bool(re.search(r"testing live url|analyzing|running test", body_text))

        valid = bool(re.search(r"valid amp page|amp page is valid|page is valid amp|valid amp", body_text))
        invalid = bool(
            re.search(r"not an amp page|not a valid amp|invalid amp|does not use amp|page cannot be reached", body_text)
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

        if not is_still_testing and (valid or invalid or has_view_tested_page):
            if not invalid and (valid or has_view_tested_page):
                return "PASS"
            else:
                return "FAIL"

        await page.wait_for_timeout(AMP_POLL_MS)

    return "TIMEOUT"


async def click_screenshot_tab(page: Page) -> bool:
    """Clicks the SCREENSHOT tab on the tested page view (from amp_test.py)."""
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

    try:
        await tab.wait_for(state="visible", timeout=10000)
        await tab.click(timeout=10000)
        await page.wait_for_timeout(3000)
        selected = await tab.get_attribute("aria-selected")
        return selected != "false"
    except Exception:
        return False


async def open_amp_test_via_google(page: Page) -> bool:
    """
    Navigates to Google, searches for 'Google AMP Test', and clicks the search result link.
    """
    try:
        print("    [AMP] 🔎 Navigating to Google to search for AMP Test...")
        await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(random_delay(1000, 2000))

        # Human/child mouse play on Google home
        await child_like_mouse_play(page, moves=2)

        # Handle cookie consent / popups if present
        for consent_btn_selector in [
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
            "button:has-text('Reject all')",
            "button:has-text('Setuju semua')",
            "button:has-text('Saya setuju')",
        ]:
            try:
                btn = page.locator(consent_btn_selector).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    break
            except Exception:
                pass

        # Locate Google search input
        search_box = page.locator("textarea[name='q'], input[name='q']").first
        await search_box.wait_for(state="visible", timeout=10_000)
        
        # Type search query with child/human-like cadence
        await child_like_human_type(page, search_box, "Google AMP Test")

        await human_delay(200, 450)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(random_delay(1500, 2500))

        # Playful mouse exploration on search results page
        await child_like_mouse_play(page, moves=random.randint(2, 4))

        # Find the search result link for Google AMP Test
        amp_link = page.locator("a[href*='search.google.com/test/amp']").first
        if await amp_link.count() == 0:
            amp_link = page.locator("a:has-text('AMP Test'), a:has-text('Google Search Console')").first

        if await amp_link.count() > 0:
            print("    [AMP] 🖱️ Clicking Google AMP Test result link...")
            box = await amp_link.bounding_box()
            if box:
                await human_mouse_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await human_delay(150, 400)
            await amp_link.click()
        else:
            print("    [AMP] ⚠️ Result link not found directly, opening https://search.google.com/test/amp...")
            await page.goto("https://search.google.com/test/amp", wait_until="domcontentloaded", timeout=30_000)

        await page.wait_for_load_state("domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)
        return True
    except Exception as e:
        print(f"    [AMP] ⚠️ Search navigation error ({e}), navigating directly to AMP test page...")
        try:
            await page.goto("https://search.google.com/test/amp", wait_until="domcontentloaded", timeout=30_000)
            return True
        except Exception:
            return False


async def amp_test_and_save(
    page: Page,
    url: str,
    base: Path,
) -> str:
    """
    Runs Google AMP Test:
      1. First searches Google for 'Google AMP Test' and clicks the search result link
      2. Types the target link into the URL input field (human/child-like typing + mouse play)
      3. Submits the test (clicks 'TEST URL' or presses Enter)
      4. Waits up to 120s for AMP results to appear
      5. Saves full results screenshot as <base>_amp_test.png
      6. If AMP is valid, clicks 'VIEW TESTED PAGE' -> 'SCREENSHOT' tab and saves <base>_tested_page.png
      7. Deletes cookies and cache after the AMP test
    """
    amp_shot_path = str(base) + "_amp_test.png"
    tested_page_path = str(base) + "_tested_page.png"
    status = "ERROR"

    try:
        # Step 1: Open AMP test page via Google search
        await open_amp_test_via_google(page)

        # Handle any existing 'Dismiss' error modal if present
        dismiss_btn = page.locator("button:has-text('Dismiss'), div[role='button']:has-text('Dismiss')").first
        if await dismiss_btn.count() > 0 and await dismiss_btn.is_visible():
            try:
                await dismiss_btn.click(timeout=3000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        # Human/child mouse play on AMP test page
        await child_like_mouse_play(page, moves=random.randint(2, 3))

        # Step 2: Locate URL input field on AMP test page
        url_input = page.locator("input[type='url'], input[aria-label*='URL' i], input[type='text'], input[placeholder*='URL' i]").first
        await url_input.wait_for(state="visible", timeout=15_000)

        # Type target URL with realistic child/human cadence, pauses, and typo simulation
        print(f"    [AMP] ⌨️ Typing target URL (human/child mode): {url}")
        await child_like_human_type(page, url_input, url)

        # Brief hesitation before clicking submit
        await human_delay(300, 700)
        await child_like_mouse_play(page, moves=1)

        # Step 3: Click 'TEST URL' button or press Enter
        test_btn = page.locator("div[role='button']:has-text('TEST URL'), button:has-text('TEST URL'), div[role='button'][jsname='LZQqje']").first
        if await test_btn.count() > 0 and await test_btn.is_visible():
            box = await test_btn.bounding_box()
            if box:
                await human_mouse_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await human_delay(150, 350)
            await test_btn.click()
            print("    [AMP] 🚀 Clicked 'TEST URL' button")
        else:
            await url_input.press("Enter")
            print("    [AMP] 🚀 Pressed Enter on URL input")

        print(f"    [AMP] ⏳ Waiting up to {int(AMP_TEST_TIMEOUT / 1000)}s for test results to appear...")
        status = await wait_for_amp_completion(page, max_wait_ms=AMP_TEST_TIMEOUT)

        # Allow settling time for graphs and cards to render
        await page.wait_for_timeout(3000)

        # Step 4: Take the full-page screenshot of the results page
        try:
            await page.screenshot(path=amp_shot_path, full_page=True, timeout=30_000)
            print(f"    [AMP] 📸 Results screenshot saved → {amp_shot_path}")
        except Exception as e:
            print(f"    [AMP] ⚠ Screenshot notice: {e}")

        # Step 5: If PASS (valid AMP), also capture tested page screenshot
        if status == "PASS":
            view_tested_page = (
                page.locator(
                    '[aria-label*="view tested page" i], [title*="view tested page" i], '
                    "a, button, [role='button']"
                )
                .filter(has_text=re.compile(r"view tested page", re.IGNORECASE))
                .first
            )
            try:
                if await view_tested_page.is_visible():
                    print("    [AMP] → Clicking 'VIEW TESTED PAGE'...")
                    await view_tested_page.click(timeout=10000)
                    await page.wait_for_timeout(3000)
                    if await click_screenshot_tab(page):
                        await page.screenshot(path=tested_page_path, full_page=True, timeout=30_000)
                        print(f"    [AMP] 📸 Tested page screenshot saved → {tested_page_path}")
            except Exception as e:
                print(f"    [AMP] ⚠ Tested page view notice: {e}")

        print(f"    [AMP] Final Status: {status}")
        return status

    except Exception as e:
        print(f"    [AMP] ⚠ AMP test failed: {e}")
        try:
            await page.screenshot(path=amp_shot_path, full_page=True, timeout=10_000)
        except Exception:
            pass
        return "ERROR"

    finally:
        # Step 6: Delete cookies and cache after each AMP test
        try:
            await clear_cookies_and_cache(page)
        except Exception as e:
            print(f"    [Cleaner] Notice during cleanup: {e}")


# ====================== SAVE SCRAPED PAGE ======================
async def scrape_and_save(
    page: Page,
    url: str,
    brand: str,
    rank: int,
    title: str,
    brand_out_dir: Path,
) -> None:
    """
    For each URL:
      1. Visit the page → screenshot (.png) + text (.txt) + HTML (.html)
      2. Run Google AMP Test → screenshot (_amp_test.png)
      3. Save combined metadata (.json)
    """
    safe_title = safe_filename(title) if title else f"rank{rank}"
    base_name  = f"{str(rank).zfill(3)}_{safe_title}"
    base       = brand_out_dir / base_name

    print(f"  [Rank {rank}] Visiting: {url}")

    amp_status = "SKIPPED"
    landing_status = "error"

    # 1. Visit landing page (isolated error handling)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        await page.wait_for_timeout(random_delay(1500, 3000))

        # Human mouse movement after page loads (Final.py pattern)
        await human_mouse_move(page, random.randint(200, 800), random.randint(150, 500))
        await simulate_mouse_movement(page, moves=random.randint(3, 5))

        # Human scroll (Final.py pattern)
        await human_scroll(page)
        await page.wait_for_timeout(random_delay(800, 2000))

        # 1. Screenshot (full page)
        try:
            await page.screenshot(path=str(base) + ".png", full_page=True, timeout=30_000)
        except Exception as e:
            print(f"    ⚠ Landing screenshot notice: {e}")

        # 2. Visible text
        try:
            text = await page.evaluate("() => document.body.innerText")
            Path(str(base) + ".txt").write_text(text or "", encoding="utf-8")
        except Exception as e:
            print(f"    ⚠ Text extraction notice: {e}")

        # 3. Full HTML
        try:
            html = await page.content()
            Path(str(base) + ".html").write_text(html or "", encoding="utf-8")
        except Exception as e:
            print(f"    ⚠ HTML extraction notice: {e}")

        landing_status = "ok"
    except Exception as e:
        print(f"    ⚠ Landing page visit notice: {e}")

    # 2. Google AMP Test (runs even if landing page timed out)
    try:
        amp_status = await amp_test_and_save(page, url, base)
    except Exception as e:
        print(f"    ⚠ AMP test execution failed: {e}")
        amp_status = "ERROR"

    # 3. Metadata JSON (includes AMP result)
    try:
        meta = {
            "brand":      brand,
            "rank":       rank,
            "title":      title,
            "url":        url,
            "scraped_at": datetime.now().isoformat(),
            "landing_status": landing_status,
            "landing_screenshot": str(base) + ".png",
            "amp_status": amp_status,          # PASS / FAIL / TIMEOUT / ERROR
            "amp_screenshot": str(base) + "_amp_test.png",
        }
        tested_shot = Path(str(base) + "_tested_page.png")
        if tested_shot.exists():
            meta["amp_tested_page_screenshot"] = str(tested_shot)

        Path(str(base) + ".json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print(f"    ✅ Saved → {base}.* (AMP: {amp_status})")
    except Exception as e:
        print(f"    ⚠ Metadata save notice: {e}")


# ====================== TAB WORKER ======================
async def tab_worker(
    tab_id: int,
    context: BrowserContext,
    initial_page: Optional[Page],
    queue: asyncio.Queue,
    total_jobs: int,
) -> None:
    """
    Each worker pulls (brand, rank, title, url, brand_out_dir) jobs from
    the shared queue and calls scrape_and_save on its own tab.
    """
    # Stagger tab openings
    if tab_id > 1:
        await asyncio.sleep((tab_id - 1) * TAB_OPEN_DELAY_SEC)

    if queue.empty():
        return

    page = initial_page if (tab_id == 1 and initial_page is not None) else None
    if page is None or page.is_closed():
        try:
            page = await context.new_page()
            await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)
        except Exception as e:
            print(f"[Tab {tab_id}] Error launching page: {e}")
            return

    print(f"[Tab {tab_id}] Ready (tab launch)")

    try:
        while not queue.empty():
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            brand, rank, title, url, brand_out_dir = job
            done = total_jobs - queue.qsize()
            print(f"\n[Tab {tab_id}] [{done}/{total_jobs}] {brand} → rank {rank}")

            # Ensure page is alive, recreate if closed
            try:
                if page is None or page.is_closed():
                    page = await context.new_page()
                    await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)
            except Exception as pe:
                print(f"[Tab {tab_id}] Recreating page after close: {pe}")
                try:
                    page = await context.new_page()
                    await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)
                except Exception:
                    pass

            try:
                await scrape_and_save(page, url, brand, rank, title, brand_out_dir)
            except Exception as e:
                print(f"[Tab {tab_id}] Unexpected scrape error: {e}")
            finally:
                queue.task_done()

            # Human-like delay before the next job
            if not queue.empty():
                delay = random_delay()
                print(f"[Tab {tab_id}] Waiting {round(delay/1000)}s…")
                await asyncio.sleep(delay / 1000.0)

    finally:
        if page and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass


# ====================== LOAD JOBS FROM JSON FILES ======================
def load_jobs(json_dir: str, out_root: Path):
    """
    Scan json_dir for *.json files, read each one, and return a list of
    (brand, rank, title, url, brand_out_dir) tuples — one per result link.
    Also creates the per-brand output directories up-front.
    """
    jobs = []
    json_path = Path(json_dir)
    json_files = sorted(json_path.glob("*.json"))

    if not json_files:
        print(f"[!] No JSON files found in {json_dir}")
        return jobs

    print(f"[Loader] Found {len(json_files)} JSON file(s) in {json_dir}")

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Loader] Skipping {jf.name}: {e}")
            continue

        brand   = str(data.get("brand", jf.stem)).strip()
        results = data.get("results", [])

        if not results:
            print(f"[Loader] {brand}: no results, skipping")
            continue

        brand_out_dir = out_root / safe_filename(brand)
        brand_out_dir.mkdir(parents=True, exist_ok=True)

        for result in results:
            rank  = result.get("rank", 0)
            title = result.get("title", "")
            url   = result.get("url", "").strip()
            if not url:
                continue
            jobs.append((brand, rank, title, url, brand_out_dir))

        print(f"[Loader] {brand}: {len(results)} link(s) queued")

    return jobs


# ====================== MAIN ======================
async def main() -> None:
    out_root = Path(OUTPUT_DIR)
    out_root.mkdir(parents=True, exist_ok=True)

    # 1. Collect all jobs
    jobs = load_jobs(RESULTS_JSON_DIR, out_root)
    if not jobs:
        print("[!] Nothing to scrape. Exiting.")
        return

    total_jobs = len(jobs)
    num_tabs   = min(CONCURRENT_TABS, total_jobs)

    print(f"\n{'='*62}")
    print(f"  \U0001F517 Link Scraper  (Anti-Detection Mode)")
    print(f"  Total links  : {total_jobs}")
    print(f"  Parallel tabs: {num_tabs}  (stagger {TAB_OPEN_DELAY_SEC}s)")
    print(f"  Output dir   : {out_root.resolve()}")
    print(f"  Warmup       : {'Enabled' if ENABLE_WARMUP else 'Disabled'}")
    print(f"{'='*62}\n")

    # 2. Fill the queue
    queue: asyncio.Queue = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)

    # 3. Launch browser (with random fingerprint + stealth args)
    playwright, context, initial_page = await launch_browser()

    try:
        # 4. Warmup browsing (build realistic session history — Final.py pattern)
        await warmup_browsing(initial_page)

        # 6. Spawn parallel tab workers
        tasks = [
            asyncio.create_task(
                tab_worker(
                    tab_id=t + 1,
                    context=context,
                    initial_page=(initial_page if t == 0 else None),
                    queue=queue,
                    total_jobs=total_jobs,
                )
            )
            for t in range(num_tabs)
        ]

        await asyncio.gather(*tasks)  # type: ignore
        print(f"\n✅ All done!  Results saved to: {out_root.resolve()}")

    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
    except Exception as err:
        print(f"[!] Fatal error: {err}", file=sys.stderr)
        sys.exit(1)
