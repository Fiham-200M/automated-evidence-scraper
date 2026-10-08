#!/usr/bin/env python3
"""
Stealth Browser Template v2.2
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
  browser, context, page = await create_stealth_browser()

Authorized-use only: respect each site's Terms of Service, robots.txt, and
applicable law. Intended for QA, accessibility testing, and research.
"""

import os
import asyncio
import random
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# rebrowser's Runtime.enable fix must be configured BEFORE the library is
# imported. Default it to 'addBinding' (keeps main-world access while hiding
# the CDP leak) unless the caller already set it.
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Init script (runs at document_start in the page) that removes Playwright's
# main-world signature objects. Defined as a string so Playwright
# serializes it for injection. Verified to flip isPlaywright true->false.
STRIP_PLAYWRIGHT_ARTIFACTS = """
() => {
  const hide = (k) => {
    try { delete window[k]; } catch { /* non-configurable */ }
    if (Object.prototype.hasOwnProperty.call(window, k)) {
      try { Object.defineProperty(window, k, { get: () => undefined, configurable: true }); } catch { /* sealed */ }
    }
  };
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/^__pw|pwInitScripts|playwright/i.test(k)) hide(k);
  }
  // window.chrome presence for the rare headless fallback (no-op in real Chrome).
  if (!window.chrome) window.chrome = {};
}
"""


async def create_stealth_browser(
    headless: bool = False,
    viewport: Optional[Dict[str, int]] = None,
    user_agent: Optional[str] = None,
    locale: Optional[str] = None,
    storage_state: Optional[str] = None,
    proxy: Optional[Dict[str, Any]] = None,
    no_sandbox: bool = False,
    executable_path: Optional[str] = None,
) -> Tuple[Browser, BrowserContext, Page]:
    """
    Create a stealth browser instance.

    Args:
        headless: Run headed (default: False, required for stealth)
        viewport: Viewport size (default: { width: 1280, height: 800 })
        user_agent: Custom user agent (optional; defaults to real Chrome UA)
        locale: Browser locale (default: None = keep real Chrome languages). Emulating a locale overrides navigator.languages + Accept-Language on the MAIN thread only; workers keep Chrome's real list, so it reintroduces a main/worker mismatch that some detectors (deviceandbrowserinfo) flag. Leave None for best stealth; set it only when a site needs a specific language.
        storage_state: Path to saved session state for cookie persistence (optional)
        proxy: Proxy config { server, username?, password? } (optional)
        no_sandbox: Add --no-sandbox (opt-in: needed for Linux root/CI, but is itself a bot signal — off by default)
        executable_path: Path to Chrome executable (optional)

    Returns:
        Tuple of (browser, context, page)
    """
    if viewport is None:
        viewport = {"width": 1280, "height": 800}

    playwright = await async_playwright().start()

    launch_options: Dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if executable_path:
        launch_options["executable_path"] = executable_path
    else:
        launch_options["channel"] = "chrome"
    # --no-sandbox is a security risk AND an automation signal; opt in only when
    # the environment requires it (e.g. running as root in Linux CI).
    if no_sandbox:
        launch_options["args"].append("--no-sandbox")
    if proxy:
        launch_options["proxy"] = proxy

    browser = await playwright.chromium.launch(**launch_options)

    # Locale is left unset by default. An emulated `locale` overrides
    # navigator.languages + Accept-Language on the MAIN thread only; workers keep
    # real Chrome's language list, producing a main/worker mismatch that
    # deviceandbrowserinfo flags as `hasInconsistentWorkerValues`. Keeping the
    # real environment untouched makes both threads report the same list. Opt in
    # only when a site needs a specific language and worker checks don't matter.
    context_options: Dict[str, Any] = {"viewport": viewport}
    if locale:
        context_options["locale"] = locale
    if user_agent:
        context_options["user_agent"] = user_agent
    if storage_state:
        context_options["storage_state"] = storage_state

    context = await browser.new_context(**context_options)

    # Strip Playwright's main-world artifacts on every navigation. This is the
    # ONLY init script — it touches nothing on navigator.
    await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)

    page = await context.new_page()

    return browser, context, page


async def save_session(context: BrowserContext, path: str) -> None:
    """
    Save session state for cookie persistence.

    Args:
        context: BrowserContext
        path: File path to save state
    """
    await context.storage_state(path=path)


async def human_delay(min_ms: int = 100, max_ms: int = 500) -> None:
    """
    Add a human-like random delay between actions.

    Args:
        min_ms: Minimum delay in ms
        max_ms: Maximum delay in ms
    """
    delay = random.random() * (max_ms - min_ms) + min_ms
    await asyncio.sleep(delay / 1000.0)


async def human_type(page: Page, selector: str, text: str) -> None:
    """
    Type text with human-like speed.

    Args:
        page: Playwright page
        selector: Element selector
        text: Text to type
    """
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await human_delay(50, 150)


async def simulate_mouse_movement(page: Page, moves: Optional[int] = None) -> None:
    """
    Simulate natural mouse movement on the page.
    Helps avoid Cloudflare Turnstile behavioral detection.

    Args:
        page: Playwright page
        moves: Number of movements (default: random 5-10)
    """
    count = moves if moves is not None else 5 + int(random.random() * 5)
    for _ in range(count):
        await page.mouse.move(
            100 + random.random() * 600,
            100 + random.random() * 400,
            steps=10,
        )
        await human_delay(50, 200)


# CLI: run this file directly to open a stealth browser at bot.sannysoft.com.
if __name__ == "__main__":
    async def main():
        print("Testing stealth browser...")
        browser, context, page = await create_stealth_browser()

        try:
            await page.goto("https://bot.sannysoft.com")
            print("Browser opened. Check results in the browser window.")
            print("Press Ctrl+C to close.")
            # Keep the browser open
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await browser.close()

    asyncio.run(main())
