#!/usr/bin/env python3
"""
Stealth Browser Template v2.2 (Python Version)
Reusable factory for bot-detection-resistant browser automation.

Design principle: with `channel: 'chrome'` + headed mode (both mandated here)
real Chrome already supplies a genuine User-Agent, WebGL/GPU renderer, canvas
fingerprint, PluginArray, and self-consistent permission/hardware values.
rebrowser-playwright additionally hides the CDP `Runtime.enable` headless tell.
So this template adds ONLY two things on top of that real environment:
  1. strips Playwright's main-world artifacts (`window.__pwInitScripts` etc.),
     the deterministic signature detectors key on for `isPlaywright`;
  2. enables rebrowser's Runtime-fix (env var, set below before import).
It touches NOTHING on `navigator` — hand-rolled fakes (PluginArray, canvas
noise, hardcoded hardwareConcurrency, a permissions override, a webdriver
delete, a languages getter) were removed across v2.1/v2.2 because each created
a detectable inconsistency (verified against bot-detector.rebrowser.net,
deviceandbrowserinfo.com, browserscan.net — all green after the cleanup).

MEASURED 2026-06-10, re-verified 2026-08-27 (macOS, headed Chrome): passes
bot.sannysoft.com, bot-detector.rebrowser.net (runtime-enable leak clean),
deviceandbrowserinfo ("human", with default null locale — see note above),
browserscan ("Normal"). Does NOT defeat IP-reputation / behavioral / login
walls, and `window.__playwright_builtins__` (a separate, non-configurable
Playwright global) cannot be stripped — see SKILL.md "Detection Coverage".

Usage:
  from stealth_template import create_stealth_browser, human_delay, human_type, simulate_mouse_movement
  session = await create_stealth_browser()
  browser, context, page = session.browser, session.context, session.page

Authorized-use only: respect each site's Terms of Service, robots.txt, and
applicable law. Intended for QA, accessibility testing, and research.
"""

import asyncio
import math
import os
import random
import sys
from typing import Any, Dict, Optional, Tuple

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

# rebrowser's Runtime.enable fix must be configured BEFORE the library is
# imported. Default it to 'addBinding' (keeps main-world access while hiding
# the CDP leak) unless the caller already set it.
if "REBROWSER_PATCHES_RUNTIME_FIX_MODE" not in os.environ:
    os.environ["REBROWSER_PATCHES_RUNTIME_FIX_MODE"] = "addBinding"

try:
    from rebrowser_playwright.async_api import async_playwright, Browser, BrowserContext, Page
except ImportError:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page


# Init script (runs at document_start in the page) that removes Playwright's
# main-world signature objects. Verified to flip isPlaywright true->false.
STRIP_PLAYWRIGHT_ARTIFACTS_JS = """
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
  // window.chrome presence for the rare headless fallback (no-op in real Chrome).
  if (!window.chrome) window.chrome = {};
})()
"""


def strip_playwright_artifacts() -> str:
    """Returns the JavaScript init script that removes Playwright's main-world signature objects."""
    return STRIP_PLAYWRIGHT_ARTIFACTS_JS


stripPlaywrightArtifacts = strip_playwright_artifacts


class StealthBrowserSession:
    """
    Session container providing flexible access to browser, context, and page:
    - Attribute access: session.browser, session.context, session.page
    - Dict access: session['browser'], session['context'], session['page']
    - Tuple unpacking: browser, context, page = session
    """
    def __init__(self, browser: Browser, context: BrowserContext, page: Page, playwright_instance: Any = None):
        self.browser = browser
        self.context = context
        self.page = page
        self._playwright_instance = playwright_instance

    def __iter__(self):
        return iter((self.browser, self.context, self.page))

    def __getitem__(self, item: str):
        if item == "browser":
            return self.browser
        elif item == "context":
            return self.context
        elif item == "page":
            return self.page
        elif item == "playwright":
            return self._playwright_instance
        raise KeyError(f"Key '{item}' not found in StealthBrowserSession")

    def get(self, key: str, default: Any = None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return ["browser", "context", "page"]

    def values(self):
        return [self.browser, self.context, self.page]

    def items(self):
        return [("browser", self.browser), ("context", self.context), ("page", self.page)]


async def create_stealth_browser(options: Optional[Dict[str, Any]] = None, **kwargs) -> StealthBrowserSession:
    """
    Create a stealth browser instance.

    :param options: Optional configuration dictionary:
        - headless (bool): Run headed (default: False, required for stealth)
        - viewport (dict): Viewport size (default: {'width': 1280, 'height': 800})
        - userAgent / user_agent (str): Custom user agent (default: real Chrome UA)
        - locale (str): Browser locale (default: None = keep real Chrome languages)
        - storageState / storage_state (str): Path to saved session state for cookie persistence
        - proxy (dict): Proxy config {'server': ..., 'username': ..., 'password': ...}
        - noSandbox / no_sandbox (bool): Add --no-sandbox (default: False)
    :return: StealthBrowserSession containing browser, context, and page.
    """
    opts = dict(options or {})
    opts.update(kwargs)

    headless = opts.get("headless", False)
    viewport = opts.get("viewport", {"width": 1280, "height": 800})
    user_agent = opts.get("userAgent") or opts.get("user_agent", None)
    locale = opts.get("locale", None)
    storage_state = opts.get("storageState") or opts.get("storage_state", None)
    proxy = opts.get("proxy", None)
    no_sandbox = opts.get("noSandbox") or opts.get("no_sandbox", False)

    launch_args = ["--disable-blink-features=AutomationControlled"]
    if no_sandbox:
        launch_args.append("--no-sandbox")

    launch_options: Dict[str, Any] = {
        "headless": headless,
        "channel": "chrome",
        "args": launch_args,
    }
    if proxy:
        launch_options["proxy"] = proxy

    p = await async_playwright().start()

    try:
        browser = await p.chromium.launch(**launch_options)
    except Exception:
        # Fallback to bundled chromium if Google Chrome is not installed at standard location
        launch_options.pop("channel", None)
        browser = await p.chromium.launch(**launch_options)

    # Wrap browser.close to ensure the Playwright driver process stops cleanly
    original_browser_close = browser.close

    async def custom_browser_close():
        try:
            await original_browser_close()
        finally:
            await p.stop()

    browser.close = custom_browser_close

    context_options: Dict[str, Any] = {"viewport": viewport}
    if locale:
        context_options["locale"] = locale
    if user_agent:
        context_options["user_agent"] = user_agent
    if storage_state:
        context_options["storage_state"] = storage_state

    context = await browser.new_context(**context_options)

    # Strip Playwright's main-world artifacts on every navigation.
    # This is the ONLY init script — it touches nothing on navigator.
    await context.add_init_script(strip_playwright_artifacts())

    page = await context.new_page()

    return StealthBrowserSession(browser=browser, context=context, page=page, playwright_instance=p)


createStealthBrowser = create_stealth_browser


async def save_session(context: BrowserContext, path: str) -> None:
    """
    Save session state for cookie persistence.
    :param context: BrowserContext
    :param path: File path to save state
    """
    await context.storage_state(path=path)


saveSession = save_session


async def human_delay(min_ms: float = 100, max_ms: float = 500) -> None:
    """
    Add a human-like random delay between actions.
    :param min_ms: Minimum delay in ms
    :param max_ms: Maximum delay in ms
    """
    delay = random.uniform(min_ms, max_ms)
    await asyncio.sleep(delay / 1000.0)


humanDelay = human_delay


async def human_type(page: Page, selector: str, text: str) -> None:
    """
    Type text with human-like speed.
    :param page: Playwright page
    :param selector: Element selector
    :param text: Text to type
    """
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await human_delay(50, 150)


humanType = human_type


async def simulate_mouse_movement(page: Page, moves: Optional[int] = None) -> None:
    """
    Simulate natural mouse movement on the page.
    Helps avoid Cloudflare Turnstile behavioral detection.
    :param page: Playwright page
    :param moves: Number of movements (default: random 5-10)
    """
    count = moves if moves is not None else (5 + math.floor(random.random() * 5))
    for _ in range(count):
        await page.mouse.move(
            100 + random.random() * 600,
            100 + random.random() * 400,
            steps=10,
        )
        await human_delay(50, 200)


simulateMouseMovement = simulate_mouse_movement


# CLI: run this file directly to open a stealth browser at bot.sannysoft.com.
async def _cli_main():
    print("Testing stealth browser...")
    session = await create_stealth_browser()
    browser = session.browser
    page = session.page

    try:
        await page.goto("https://bot.sannysoft.com")
        print("Browser opened. Check results in the browser window.")
        print("Press Ctrl+C to close.")
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        pass
