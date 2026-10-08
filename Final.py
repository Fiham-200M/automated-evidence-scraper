import os
import sys
from pathlib import Path

# ── rebrowser's Runtime.enable fix MUST be set BEFORE importing the library ──
if "REBROWSER_PATCHES_RUNTIME_FIX_MODE" not in os.environ:
    os.environ["REBROWSER_PATCHES_RUNTIME_FIX_MODE"] = "addBinding"

# Ensure Windows terminal outputs Unicode cleanly without charmap crashes
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

from rebrowser_playwright.async_api import async_playwright
import asyncio
import math
import random
import json
import threading
import requests
import urllib.parse
import subprocess
import urllib.request
import pydub
import speech_recognition as sr
from datetime import datetime, timedelta


# ====================== FFMPEG CONFIGURATION ======================
def setup_ffmpeg():
    """Automatically configure ffmpeg & ffprobe for audio conversion."""
    bin_dir = Path("bin").resolve()
    ffmpeg_exe = bin_dir / "ffmpeg.exe"
    ffprobe_exe = bin_dir / "ffprobe.exe"
    if ffmpeg_exe.exists():
        pydub.AudioSegment.converter = str(ffmpeg_exe)
    if ffprobe_exe.exists():
        pydub.AudioSegment.prober = str(ffprobe_exe)
        pydub.AudioSegment.ffprobe = str(ffprobe_exe)
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


setup_ffmpeg()
# ==================================================================


# ====================== STEALTH INIT SCRIPT =======================
# From mathi/stealth_template2.py — IIFE form, strips Playwright
# main-world artifacts on every page navigation without touching navigator.
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
    """Returns the JS init script that removes Playwright's main-world signature objects."""
    return STRIP_PLAYWRIGHT_ARTIFACTS_JS


# ==================================================================


# ====================== RECAPTCHA SOLVER ==========================
class PlaywrightRecaptchaSolver:
    """reCAPTCHA solver designed natively for Playwright / rebrowser-playwright."""
    TEMP_DIR = Path(os.getenv("TEMP") or "/tmp")
    TIMEOUT = 8

    def __init__(self, page):
        self.page = page

    @property
    def anchor_frame(self):
        """The checkbox iframe."""
        return self.page.frame_locator('iframe[src*="anchor"], iframe[title="reCAPTCHA"]').first

    @property
    def challenge_frame(self):
        """The popup challenge (images / audio) iframe."""
        return self.page.frame_locator(
            'iframe[src*="bframe"], iframe[title*="challenge" i], iframe[title*="expires in two minutes" i]'
        ).first

    async def is_captcha_present(self) -> bool:
        """Check if reCAPTCHA is currently on the page and unsolved."""
        try:
            if "sorry/index" in self.page.url:
                return True
            anchor_count = await self.page.locator(
                'iframe[src*="anchor"], iframe[title="reCAPTCHA"]'
            ).count()
            if anchor_count > 0:
                return not await self.is_solved()
            return False
        except Exception:
            return False

    async def is_solved(self) -> bool:
        """Check if reCAPTCHA is successfully verified."""
        try:
            anchor = self.anchor_frame.locator("#recaptcha-anchor")
            if await anchor.count() > 0:
                checked = await anchor.get_attribute("aria-checked", timeout=1000)
                if checked == "true":
                    return True
        except Exception:
            pass

        try:
            count = await self.page.locator(
                'iframe[src*="anchor"], iframe[title="reCAPTCHA"]'
            ).count()
            if count == 0 and "sorry" not in self.page.url:
                return True
        except Exception:
            pass

        return False

    async def is_detected(self) -> bool:
        """Check if Google detected bot activity (Try again later)."""
        try:
            msg = self.challenge_frame.locator(
                ".rc-doscaptcha-body-text, :text('Try again later'), :text('automated queries')"
            )
            if await msg.count() > 0 and await msg.first.is_visible(timeout=500):
                return True
        except Exception:
            pass

        try:
            if await self.page.locator(":text('Try again later')").is_visible(timeout=500):
                return True
        except Exception:
            pass

        return False

    async def is_challenge_open(self) -> bool:
        """Check if the challenge popup iframe is currently open on screen."""
        try:
            audio_btn = self.challenge_frame.locator("#recaptcha-audio-button")
            audio_src = self.challenge_frame.locator(
                "#audio-source, a.rc-audiochallenge-tdownload-link, a[href*='audio.mp3']"
            )
            audio_input = self.challenge_frame.locator("#audio-response")
            verify_btn = self.challenge_frame.locator("#recaptcha-verify-button")

            if await audio_btn.count() > 0 and await audio_btn.first.is_visible(timeout=800):
                return True
            if await audio_src.count() > 0 and await audio_src.first.is_visible(timeout=800):
                return True
            if await audio_input.count() > 0 and await audio_input.first.is_visible(timeout=800):
                return True
            if await verify_btn.count() > 0 and await verify_btn.first.is_visible(timeout=800):
                return True
        except Exception:
            pass
        return False

    async def solve(self, max_rounds: int = 6) -> bool:
        for round_num in range(1, max_rounds + 1):
            print(f"[Captcha] Round {round_num}/{max_rounds}")

            if not await self.is_captcha_present():
                print("[Captcha] No captcha present")
                return True

            if await self.is_solved():
                print("[Captcha] Solved successfully")
                return True

            try:
                await self._solve_single()
            except Exception as e:
                print(f"[Captcha] Round {round_num} note: {e}")

            await self.page.wait_for_timeout(2000)

            if not await self.is_captcha_present() or await self.is_solved():
                print("[Captcha] Solved successfully!")
                return True

        print("[Captcha] Failed to solve after max rounds")
        return False

    async def _solve_single(self):
        # 1. If challenge popup is not open, click the checkbox
        challenge_open = await self.is_challenge_open()
        if not challenge_open:
            print("[Captcha] Clicking checkbox...")
            try:
                checkbox = self.anchor_frame.locator("#recaptcha-anchor, .rc-anchor-content").first
                await checkbox.click(timeout=6000, force=True)
                await self.page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[Captcha] Checkbox click: {e}")

            if await self.is_solved():
                print("[Captcha] Solved by checkbox alone")
                return

        # 2. Check if Google blocked with "Try again later"
        if await self.is_detected():
            raise Exception("Bot detected by Google (Try again later)")

        # 3. Switch to audio challenge if not already on audio screen
        audio_input = self.challenge_frame.locator("#audio-response")
        audio_input_visible = False
        try:
            audio_input_visible = (
                await audio_input.count() > 0
                and await audio_input.first.is_visible(timeout=1000)
            )
        except Exception:
            audio_input_visible = False

        if not audio_input_visible:
            audio_btn = self.challenge_frame.locator("#recaptcha-audio-button")
            try:
                if await audio_btn.count() > 0 and await audio_btn.first.is_visible(timeout=3000):
                    print("[Captcha] Switching to audio challenge...")
                    await audio_btn.first.click(timeout=6000, force=True)
                    await self.page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[Captcha] Audio button click issue: {e}")

        if await self.is_detected():
            raise Exception("Bot detected by Google (Try again later)")

        # 4. Locate audio source URL
        audio_url = None
        try:
            elem = self.challenge_frame.locator("#audio-source")
            if await elem.count() > 0:
                audio_url = await elem.first.get_attribute("src", timeout=4000)
        except Exception:
            pass

        if not audio_url:
            try:
                elem = self.challenge_frame.locator(
                    "a.rc-audiochallenge-tdownload-link, a[href*='audio.mp3']"
                )
                if await elem.count() > 0:
                    audio_url = await elem.first.get_attribute("href", timeout=4000)
            except Exception:
                pass

        if not audio_url:
            if await self.is_detected():
                raise Exception("Bot detected by Google (Try again later)")
            raise Exception("Could not find audio source URL")

        print("[Captcha] Processing audio challenge...")

        # 5. Download and transcribe speech
        text = await asyncio.to_thread(self._process_audio, audio_url)
        print(f"[Captcha] Recognized text: {text}")

        if not text or not text.strip():
            raise Exception("Speech transcription returned empty text")

        # 6. Fill audio response and click Verify
        input_field = self.challenge_frame.locator("#audio-response").first
        await input_field.fill(text.strip().lower())
        await self.page.wait_for_timeout(500)

        verify_btn = self.challenge_frame.locator("#recaptcha-verify-button").first
        await verify_btn.click(timeout=6000, force=True)
        await self.page.wait_for_timeout(2500)

    def _process_audio(self, audio_url: str) -> str:
        setup_ffmpeg()
        mp3_path = self.TEMP_DIR / f"rc_{random.randint(10000, 99999)}.mp3"
        wav_path = self.TEMP_DIR / f"rc_{random.randint(10000, 99999)}.wav"

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            }
            req = urllib.request.Request(audio_url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp, open(mp3_path, "wb") as f:
                f.write(resp.read())

            # Convert MP3 to WAV using local ffmpeg binary directly
            bin_ffmpeg = Path("bin/ffmpeg.exe").resolve()
            if bin_ffmpeg.exists():
                subprocess.run(
                    [str(bin_ffmpeg), "-y", "-i", str(mp3_path), str(wav_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            else:
                sound = pydub.AudioSegment.from_mp3(str(mp3_path))
                sound.export(str(wav_path), format="wav")

            recognizer = sr.Recognizer()
            with sr.AudioFile(str(wav_path)) as source:
                audio_data = recognizer.record(source)

            return recognizer.recognize_google(audio_data)
        finally:
            for p in (mp3_path, wav_path):
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass


# ====================== CONFIG & SCHEDULER ======================
# ── Search timing (from mathi/search_and_scrape2.py pattern) ──
MIN_DELAY_MS = 4000    # min delay between searches on each tab (ms)
MAX_DELAY_MS = 9000    # max delay between searches on each tab (ms)

PARALLEL_WORKERS = 5     # max active brands to search in parallel
TAB_STAGGER_DELAY = 1.5       # seconds gap between opening each new tab
MAX_TAB_RETRIES = 5            # max fresh-tab attempts if captcha fails

# ── Scheduler Settings ──
SCHEDULE_INTERVAL_MINUTES = 20
ENABLE_SCHEDULER = True

# ── Persistent Chrome Profile (for Gmail login) ──
CHROME_PROFILE_DIR = Path("chrome_profile").resolve()

# ── Anti-Detection Settings ──
ENABLE_WARMUP = True
WARMUP_SITES = [
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://www.bbc.com",
    "https://edition.cnn.com",
    "https://www.reuters.com",
    "https://news.ycombinator.com",
    "https://www.weather.com",
]
# ================================================================


# ====================== ANTI-DETECTION HELPERS ======================

# Pool of realistic Chrome User-Agent strings to rotate between cycles
# (no Firefox UAs — engine/header mismatch triggers detectors in Chromium)
USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

# Pool of viewport sizes to randomize browser fingerprint
VIEWPORT_POOL = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
]


def get_random_fingerprint():
    """Generate a randomized browser fingerprint for each cycle."""
    return {
        "user_agent": random.choice(USER_AGENT_POOL),
        "viewport": random.choice(VIEWPORT_POOL),
    }


def random_delay(min_ms: int = MIN_DELAY_MS, max_ms: int = MAX_DELAY_MS) -> int:
    """Return a random delay in milliseconds (from search_and_scrape2.py pattern)."""
    return math.floor(random.random() * (max_ms - min_ms + 1)) + min_ms


# ── human_delay: from mathi/stealth_template2.py ──
async def human_delay(min_ms: float = 100, max_ms: float = 500) -> None:
    """Add a human-like random delay between actions."""
    delay = random.uniform(min_ms, max_ms)
    await asyncio.sleep(delay / 1000.0)


# ── simulate_mouse_movement: from mathi/stealth_template2.py (steps=10) ──
async def simulate_mouse_movement(page, moves: int = None) -> None:
    """
    Simulate natural mouse movement on the page.
    Helps avoid Cloudflare Turnstile / Google behavioral detection.
    Uses steps=10 for smooth interpolated trajectories (stealth_template2.py).
    """
    count = moves if moves is not None else (5 + math.floor(random.random() * 5))
    for _ in range(count):
        await page.mouse.move(
            100 + random.random() * 600,
            100 + random.random() * 400,
            steps=10,
        )
        await human_delay(50, 200)


# ── human_mouse_move: Bezier curve (Final.py original — more realistic) ──
async def human_mouse_move(page, target_x, target_y, steps=None):
    """Move the mouse in a natural Bezier curve to the target coordinates."""
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
            # Variable speed — slower at start and end (ease-in-out)
            delay = random.uniform(0.005, 0.025) * (1 + 0.5 * math.sin(math.pi * t))
            await asyncio.sleep(delay)
    except Exception:
        await page.mouse.move(target_x, target_y)


# ── human_type: from mathi/stealth_template2.py (keyboard.type per char) ──
async def human_type(page, selector: str, text: str) -> None:
    """Type text with human-like per-character speed using keyboard.type."""
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await human_delay(50, 150)


async def human_scroll(page):
    """Perform random human-like scrolling on the current page."""
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


async def warmup_browsing(page):
    """Visit a few random benign websites to build realistic session history."""
    if not ENABLE_WARMUP:
        return

    sites = random.sample(WARMUP_SITES, k=min(random.randint(1, 3), len(WARMUP_SITES)))
    print(f"🌐 [Warmup] Visiting {len(sites)} random site(s) to build browsing history...")

    for site in sites:
        try:
            await page.goto(site, wait_until="domcontentloaded", timeout=15000)
            # Use page.wait_for_timeout for in-browser timing (search_and_scrape2.py pattern)
            await page.wait_for_timeout(random.randint(2000, 5000))

            await human_scroll(page)
            await page.wait_for_timeout(random.randint(1000, 3000))

            # Both Bezier curve + natural multi-step movement
            await human_mouse_move(page, random.randint(200, 800), random.randint(150, 500))
            await simulate_mouse_movement(page)

            print(f"🌐 [Warmup] Visited: {site.split('/')[2]}")
        except Exception as e:
            print(f"🌐 [Warmup] Skipped {site}: {e}")

    try:
        await page.goto("about:blank")
    except Exception:
        pass
    print("🌐 [Warmup] Done.\n")


# ====================== LOAD BRANDS ======================
def load_brands():
    candidates = [
        Path(r".\brand.json"),
        Path(r"C:\Users\Thushalika\Task 1\brand.json"),
    ]
    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict) and "brands" in data:
                        return data["brands"]
            except Exception as e:
                print(f"[!] Error loading brands from {p}: {e}")
    print("[!] Warning: Could not find brand.json in current directory or Downloads.")
    return []


# ====================== OUTPUT DIRECTORIES ======================
screenshots_dir = Path("screenshots")
screenshots_dir.mkdir(exist_ok=True)

json_dir = Path("results_json")
json_dir.mkdir(exist_ok=True)

txt_dir = Path("results_txt")
txt_dir.mkdir(exist_ok=True)

results_file = Path("search_results.txt")
master_json_file = Path("search_results.json")
history_json_file = Path("all_search_results_history.json")

# HTTP session for redirect resolution
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
})

file_lock = threading.Lock()
completed_counter = {"count": 0}
counter_lock = threading.Lock()

# Async lock to enforce TAB_STAGGER_DELAY between opening new tabs
launch_lock = asyncio.Lock()


# ====================== URL RESOLUTION ======================
def resolve_redirect_url(url: str) -> str:
    if not url:
        return url

    if "/url?" in url and ("q=" in url or "url=" in url):
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "q" in params and params["q"]:
            return params["q"][0]
        if "url" in params and params["url"]:
            return params["url"][0]

    if "/goto?url=" in url or "/url?" in url:
        try:
            resp = http_session.get(url, allow_redirects=False, timeout=5)
            if resp.is_redirect and "Location" in resp.headers:
                target = resp.headers["Location"]
                if target.startswith("/"):
                    target = f"https://www.google.com{target}"
                return target
        except Exception:
            pass

    return url


# ====================== GOOGLE LOGIN ======================
async def is_google_logged_in(context) -> bool:
    """Check if the browser context has active Google authentication cookies."""
    try:
        cookies = await context.cookies(["https://www.google.com", "https://accounts.google.com"])
        return any(c.get("name") in ("SID", "HSID", "SSID") for c in cookies)
    except Exception:
        return False


async def ensure_google_login(context):
    """Ensure that the persistent Chrome profile is logged into a Google/Gmail account."""
    if await is_google_logged_in(context):
        print("👤 [Account] Google/Gmail login detected and active.")
        return

    print("\n" + "=" * 65)
    print("⚠️ [LOGIN REQUIRED] Google / Gmail account is not yet logged in.")
    print("Opening Google sign-in page in Chrome...")
    print("👉 Please sign in to your Gmail/Google account in the browser.")
    print("👉 Once logged in, the script will automatically detect it and proceed.")
    print("(This only needs to be done once — your session is saved in 'chrome_profile')")
    print("=" * 65 + "\n")

    login_page = context.pages[0] if context.pages else await context.new_page()
    try:
        await login_page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded")
    except Exception as e:
        print(f"[!] Notice navigating to sign-in page: {e}")

    for _ in range(90):
        await asyncio.sleep(2)
        if await is_google_logged_in(context):
            print("\n✅ Google login detected! Account session saved successfully.\n")
            try:
                await login_page.goto("about:blank")
            except Exception:
                pass
            return

    print("⚠️ Notice: Login not detected within timeout. Continuing with current session...")


# ====================== CACHE CLEARING ======================
async def clear_cycle_cache_and_cookies(context, label="Cycle"):
    """Clear browser cache and search cookies while preserving Google/Gmail login session."""
    try:
        all_cookies = await context.cookies()
        auth_cookie_prefixes = ("__Secure-", "__Host-")
        auth_cookie_names = {
            "SID", "SSID", "APISID", "HSID", "SAPISID",
            "SIDCC", "LOGIN_INFO", "ACCOUNT_CHOOSER", "OTZ",
        }

        auth_cookies = [
            c for c in all_cookies
            if c.get("name") in auth_cookie_names
            or any(c.get("name", "").startswith(p) for p in auth_cookie_prefixes)
        ]

        await context.clear_cookies()
        if auth_cookies:
            await context.add_cookies(auth_cookies)

        dummy_page = await context.new_page()
        cdp = await context.new_cdp_session(dummy_page)
        await cdp.send("Network.clearBrowserCache")
        await dummy_page.close()
        print(f"🧹 [{label}] Browser cache and search cookies cleared (Google login preserved).")
    except Exception as e:
        print(f"[!] [{label}] Cache clear notice: {e}")


# ====================== SINGLE TAB SEARCH ======================
async def search_brand_single_tab(context, brand, index=1, total=1, attempt=1, tab_id=1, cycle_info=None) -> bool:
    """
    Perform a Google search for a brand in a single fresh tab.
    Uses page.wait_for_timeout() for in-browser delays (search_and_scrape2.py pattern).
    Returns True on success, False if captcha/detection blocked.
    """
    # Enforce stagger delay before opening the new tab (search_and_scrape2.py stagger pattern)
    async with launch_lock:
        page = await context.new_page()
        if attempt > 1:
            print(f"[Tab #{tab_id}] [{index}/{total}] Retry tab #{attempt} for: {brand} (staggering {TAB_STAGGER_DELAY}s)")
        else:
            print(f"[Tab #{tab_id}] Tab opened and ready.")
            print(f"[Tab #{tab_id}] [{index}/{total}] Searching brand: \"{brand}\"")
        await asyncio.sleep(TAB_STAGGER_DELAY)

    try:
        # 1. Navigate to Google homepage first (warm navigation)
        await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=45000)
        # page.wait_for_timeout instead of asyncio.sleep for in-browser timing
        await page.wait_for_timeout(random.randint(2500, 5000))

        # 2. Check for initial captcha on homepage
        solver = PlaywrightRecaptchaSolver(page)
        if await solver.is_captcha_present():
            print(f"[Tab #{tab_id}] [{index}/{total}] Captcha detected before search for {brand} — solving...")
            solved = await solver.solve(max_rounds=6)
            if not solved:
                print(f"[Tab #{tab_id}] [{index}/{total}] Captcha could not be solved before search for {brand}")
                return False

        # 3. Human-like mouse movement: Bezier curve + natural multi-step
        await human_mouse_move(page, random.randint(300, 800), random.randint(200, 500))
        await simulate_mouse_movement(page)
        await page.wait_for_timeout(random.randint(800, 1800))

        # 4. Optional scroll — 40% chance (mimics reading behaviour)
        if random.random() < 0.4:
            await human_scroll(page)
            await page.wait_for_timeout(random.randint(500, 1500))

        # 5. Navigate directly to search URL (udm=14 forces clean Web tab)
        search_url = f"https://www.google.com/search?q={urllib.parse.quote(brand)}&udm=14"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)

        # Human mouse movement on results page (from stealth_template2.py)
        await page.wait_for_timeout(random.randint(3500, 7000))
        await simulate_mouse_movement(page, moves=random.randint(3, 6))

        # 6. Scroll results page like a human (from search_and_scrape2.py)
        await page.evaluate("""() => {
            window.scrollBy(0, Math.floor(Math.random() * 600) + 200);
        }""")
        await page.wait_for_timeout(random.randint(600, 1500))

        # 7. Wait for results to settle (search_and_scrape2.py pattern)
        await page.wait_for_selector("body", timeout=15000)

        # 8. Captcha check on search results
        if await solver.is_captcha_present():
            print(f"[Tab #{tab_id}] [{index}/{total}] Captcha detected on results for {brand} — solving...")
            success = await solver.solve(max_rounds=6)
            if not success:
                print(f"[Tab #{tab_id}] [{index}/{total}] Could not solve captcha for {brand}")
                return False
            await page.wait_for_timeout(2000)
            await page.wait_for_load_state("domcontentloaded")

        # 9. Verify page is not still blocked
        if await solver.is_captcha_present() or "sorry/index" in page.url:
            print(f"[Tab #{tab_id}] [{index}/{total}] Captcha still blocking results for {brand}")
            return False

        # 10. Extract results
        results = page.locator("a h3")
        count = await results.count()
        print(f"[Tab #{tab_id}] [{index}/{total}] {brand}: Found {count} results")

        if count == 0 and (await solver.is_detected() or "sorry" in page.url):
            print(f"[Tab #{tab_id}] [{index}/{total}] Bot detection triggered on '{brand}'")
            return False

        search_url = page.url

        # ── Determine output paths ──
        if cycle_info is None:
            curr_screenshots_dir = screenshots_dir
            curr_json_dir = json_dir
            curr_txt_dir = txt_dir
            curr_master_json = master_json_file
            curr_results_txt = results_file
            cycle_number = 1
            timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            curr_screenshots_dir = cycle_info["screenshots_dir"]
            curr_json_dir = cycle_info["json_dir"]
            curr_txt_dir = cycle_info["txt_dir"]
            curr_master_json = cycle_info["cycle_master_json"]
            curr_results_txt = cycle_info["cycle_results_txt"]
            cycle_number = cycle_info["cycle_num"]
            timestamp_str = cycle_info["readable_time"]

        safe_name = "".join(
            c if c.isalnum() or c in (" ", "-", "_") else "_" for c in str(brand)
        ).strip().replace(" ", "_")

        screenshot_path = curr_screenshots_dir / f"{safe_name}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"[Tab #{tab_id}] [{index}/{total}] {brand}: Screenshot saved → {screenshot_path}")

        with counter_lock:
            completed_counter["count"] += 1
            done = completed_counter["count"]
        balance = total - done

        # 11. Collect top-10 organic results
        brand_results = []
        for i in range(min(10, count)):
            try:
                title = await results.nth(i).inner_text()
                title = title.strip()
                link = await results.nth(i).locator("xpath=..").get_attribute("href")
                if link and link.startswith("/"):
                    link = f"https://www.google.com{link}"
                link = resolve_redirect_url(link)

                if title:
                    print(f"[Tab #{tab_id}] [{index}/{total}] {brand}: {i+1}. {title}")
                    print(f"         → {link}")
                    brand_results.append({
                        "rank": i + 1,
                        "title": title,
                        "url": link,
                    })
            except Exception:
                continue

        # ── JSON OUTPUT ──
        brand_data = {
            "cycle": cycle_number,
            "timestamp": timestamp_str,
            "index": index,
            "total": total,
            "brand": brand,
            "status": {
                "completed": done,
                "balance": balance,
                "total": total,
            },
            "search_url": search_url,
            "screenshot": str(screenshot_path),
            "results_count": count,
            "results": brand_results,
        }

        # 1. Individual brand JSON for this cycle
        brand_json_path = curr_json_dir / f"{safe_name}.json"
        with open(brand_json_path, "w", encoding="utf-8") as f:
            json.dump(brand_data, f, ensure_ascii=False, indent=2)
        print(f"[Tab #{tab_id}] [{index}/{total}] JSON saved → {brand_json_path}")

        # 2. Append to Cycle Master JSON & Global History JSON (thread-safe)
        with file_lock:
            cycle_master_data = []
            if curr_master_json.exists():
                try:
                    with open(curr_master_json, "r", encoding="utf-8") as f:
                        cycle_master_data = json.load(f)
                except Exception:
                    cycle_master_data = []
            cycle_master_data.append(brand_data)
            with open(curr_master_json, "w", encoding="utf-8") as f:
                json.dump(cycle_master_data, f, ensure_ascii=False, indent=2)

            history_data = []
            if history_json_file.exists():
                try:
                    with open(history_json_file, "r", encoding="utf-8") as f:
                        history_data = json.load(f)
                except Exception:
                    history_data = []
            history_data.append(brand_data)
            with open(history_json_file, "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)

        # 3. Individual brand text file for this cycle
        brand_txt_file = curr_txt_dir / f"{safe_name}.txt"
        with open(brand_txt_file, "w", encoding="utf-8") as f:
            f.write(f"{'='*50}\n")
            f.write(f"[Cycle #{cycle_number} | {timestamp_str}]\n")
            f.write(f"[{index}/{total}] Brand: {brand}\n")
            f.write(f"Status: {done} Completed | {balance} Balance | {total} Total\n")
            f.write(f"Search URL: {search_url}\n")
            f.write(f"Screenshot: {screenshot_path}\n")
            f.write(f"Found {count} results\n")
            f.write(f"{'='*50}\n\n")
            for item in brand_results:
                f.write(f"{item['rank']}. {item['title']}\n")
                f.write(f"   → {item['url']}\n\n")

        # 4. Append to Cycle Master TXT & Global Cumulative TXT
        with file_lock:
            if curr_results_txt != results_file:
                with open(curr_results_txt, "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*50}\n")
                    f.write(f"[{index}/{total}] Brand: {brand}\n")
                    f.write(f"Status: {done} Completed | {balance} Balance | {total} Total\n")
                    f.write(f"Search URL: {search_url}\n")
                    f.write(f"Screenshot: {screenshot_path}\n")
                    f.write(f"Found {count} results\n")
                    f.write(f"{'='*50}\n\n")
                    for item in brand_results:
                        f.write(f"{item['rank']}. {item['title']}\n")
                        f.write(f"   → {item['url']}\n\n")

            with open(results_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\n")
                f.write(f"[Cycle #{cycle_number} | {timestamp_str}] [{index}/{total}] Brand: {brand}\n")
                f.write(f"Status: {done} Completed | {balance} Balance | {total} Total\n")
                f.write(f"Search URL: {search_url}\n")
                f.write(f"Screenshot: {screenshot_path}\n")
                f.write(f"Found {count} results\n")
                f.write(f"{'='*50}\n\n")
                for item in brand_results:
                    f.write(f"{item['rank']}. {item['title']}\n")
                    f.write(f"   → {item['url']}\n\n")

        print(f"✅ [Tab #{tab_id}] [{index}/{total}] {brand} done — Completed: {done} | Balance: {balance} | Total: {total}\n")
        return True

    except Exception as err:
        print(f"[!] [Tab #{tab_id}] [{index}/{total}] Error searching '{brand}' (attempt {attempt}): {err}")
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


# ====================== SEARCH WITH RETRIES ======================
async def search_brand(context, brand, index=1, total=1, tab_id=1, cycle_info=None):
    """Search brand with automatic fresh-tab retries until good results are obtained."""
    for attempt in range(1, MAX_TAB_RETRIES + 1):
        print(f"\n{'='*60}")
        if attempt > 1:
            print(f"[Tab #{tab_id}] [{index}/{total}] Retry attempt {attempt}/{MAX_TAB_RETRIES} for: {brand}")
        else:
            print(f"[Tab #{tab_id}] [{index}/{total}] Preparing to search: {brand}")
        print(f"{'='*60}")

        success = await search_brand_single_tab(
            context, brand, index, total,
            attempt=attempt, tab_id=tab_id, cycle_info=cycle_info,
        )
        if success:
            return True

        if attempt < MAX_TAB_RETRIES:
            print(f"[!] [Tab #{tab_id}] [{index}/{total}] Attempt {attempt} failed for '{brand}'. each...")
            await asyncio.sleep(random.uniform(1.0, 2.0))

    print(f"❌ [Tab #{tab_id}] [{index}/{total}] Exhausted {MAX_TAB_RETRIES} attempts for '{brand}'.")
    return False


# ====================== SINGLE CYCLE ======================
async def run_single_cycle(cycle_num=1):
    brands = load_brands()
    total_brands = len(brands)

    with counter_lock:
        completed_counter["count"] = 0

    now_dt = datetime.now()
    timestamp_str = now_dt.strftime("%Y%m%d_%H%M%S")
    readable_time = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Create cycle-specific subdirectories so nothing is ever overwritten
    cycle_tag = f"cycle_{cycle_num:03d}_{timestamp_str}"
    cycle_screenshots_dir = screenshots_dir / cycle_tag
    cycle_screenshots_dir.mkdir(parents=True, exist_ok=True)

    cycle_json_dir = json_dir / cycle_tag
    cycle_json_dir.mkdir(parents=True, exist_ok=True)

    cycle_txt_dir = txt_dir / cycle_tag
    cycle_txt_dir.mkdir(parents=True, exist_ok=True)

    cycle_master_json = cycle_json_dir / "search_results.json"
    cycle_results_txt = cycle_txt_dir / "search_results.txt"

    cycle_info = {
        "cycle_num": cycle_num,
        "timestamp_str": timestamp_str,
        "readable_time": readable_time,
        "screenshots_dir": cycle_screenshots_dir,
        "json_dir": cycle_json_dir,
        "txt_dir": cycle_txt_dir,
        "cycle_master_json": cycle_master_json,
        "cycle_results_txt": cycle_results_txt,
    }

    # Write cycle header to global search_results.txt
    with file_lock:
        with open(results_file, "a", encoding="utf-8") as f:
            f.write(f"\n\n{'#'*70}\n")
            f.write(f"### [CYCLE #{cycle_num}] STARTED AT: {readable_time} | TOTAL BRANDS: {total_brands}\n")
            f.write(f"{'#'*70}\n")

    print(f"\n{'='*65}")
    print(f"🚀 [CYCLE #{cycle_num}] Starting run at: {readable_time}")
    print(f"   Total brands: {total_brands}")
    print(f"   Cycle outputs folder: {cycle_tag}")
    print(f"   Parallel tabs (workers): {PARALLEL_WORKERS}")
    print(f"   Tab stagger delay: {TAB_STAGGER_DELAY}s")
    print(f"   Max retries per brand: {MAX_TAB_RETRIES}")
    print(f"{'='*65}\n")

    if total_brands == 0:
        print("[!] No brands found to search. Skipping cycle.")
        return

    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    fingerprint = get_random_fingerprint()
    print(f"🎭 [Stealth] Viewport: {fingerprint['viewport']['width']}x{fingerprint['viewport']['height']}")
    print(f"🎭 [Stealth] User-Agent: {fingerprint['user_agent'][:70]}...")

    async with async_playwright() as p:
        chrome_exe = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            channel="chrome",
            executable_path=chrome_exe if os.path.exists(chrome_exe) else None,
            viewport=fingerprint["viewport"],
            # locale intentionally omitted (stealth-template.mjs):
            # emulating locale sets navigator.languages only on the MAIN thread;
            # workers keep Chrome's real list → detectable mismatch on deviceandbrowserinfo.
            timezone_id="Asia/Kolkata",
            user_agent=fingerprint["user_agent"],
            # ── stealth_template2.py: remove --enable-automation flag ──
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
            ],
        )

        try:
            # ── stealth_template2.py: inject IIFE artifact-stripping script ──
            await context.add_init_script(strip_playwright_artifacts())

            # Ensure Google/Gmail account is logged in
            await ensure_google_login(context)

            # Clear cache and non-auth cookies at start of cycle
            await clear_cycle_cache_and_cookies(context, label=f"Cycle #{cycle_num} Start")

            # Warm up browsing session — keep warmup_page as blank holder tab
            # (Chrome requires ≥1 page in persistent context to create new tabs)
            warmup_page = context.pages[0] if context.pages else await context.new_page()
            await warmup_browsing(warmup_page)
            try:
                await warmup_page.goto("about:blank")
            except Exception:
                pass

            # ── Queue-based continuous batching (search_and_scrape2.py pattern) ──
            brand_queue: asyncio.Queue = asyncio.Queue()
            for idx, brand in enumerate(brands, 1):
                brand_queue.put_nowait((idx, brand))

            async def worker(worker_id: int):
                """
                Continuous worker: pulls brands from the queue until empty.
                Stagger: tab_id (worker_id) delays match search_and_scrape2.py pattern.
                Includes inter-search random delay using page.wait_for_timeout().
                """
                # Initial stagger: worker N waits N-1 × TAB_STAGGER_DELAY before first brand
                if worker_id > 1:
                    await asyncio.sleep((worker_id - 1) * TAB_STAGGER_DELAY)

                while True:
                    try:
                        idx, brand = brand_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    try:
                        await search_brand(
                            context, brand, idx, total_brands,
                            tab_id=worker_id, cycle_info=cycle_info,
                        )
                    except Exception as err:
                        print(f"[!] [Tab #{worker_id}] Unexpected worker error: {err}")
                    finally:
                        brand_queue.task_done()

                    # Inter-search delay using random_delay() (search_and_scrape2.py)
                    if not brand_queue.empty():
                        delay = random_delay()
                        print(f"  [Tab #{worker_id}] waiting {round(delay / 1000)}s before next search...")
                        # page.wait_for_timeout unavailable here; asyncio.sleep is correct
                        await asyncio.sleep(delay / 1000.0)

                print(f"  [Tab #{worker_id}] Tab closed.")

            num_workers = min(PARALLEL_WORKERS, total_brands)
            workers = [
                asyncio.create_task(worker(w_id + 1))
                for w_id in range(num_workers)
            ]

            await asyncio.gather(*workers)

            # Clear cache and non-auth cookies at end of cycle
            await clear_cycle_cache_and_cookies(context, label=f"Cycle #{cycle_num} End")

            print(f"\n✅ Cycle #{cycle_num} completed! All {total_brands} brand searches finished.")

        finally:
            try:
                await context.close()
            except Exception:
                pass


# ====================== MAIN — SCHEDULER LOOP ======================
async def main():
    if not ENABLE_SCHEDULER:
        print("▶ Scheduler disabled. Running single execution...")
        await run_single_cycle(1)
        return

    interval_seconds = SCHEDULE_INTERVAL_MINUTES * 60
    cycle = 1
    print(f"\n{'#'*65}")
    print(f"  ⏰ REPEATING SCHEDULER ACTIVE")
    print(f"  ⏰ Schedule Interval: {SCHEDULE_INTERVAL_MINUTES} minute(s) ({interval_seconds} seconds)")
    print(f"  ⏰ Press Ctrl + C to stop the scheduler")
    print(f"{'#'*65}\n")

    while True:
        cycle_start = datetime.now()
        print(f"\n{'='*65}")
        print(f"🚀 [CYCLE #{cycle}] Starting run at: {cycle_start.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*65}")

        try:
            await run_single_cycle(cycle)
        except Exception as e:
            print(f"[!] Error during Cycle #{cycle}: {e}")

        cycle_end = datetime.now()
        duration = cycle_end - cycle_start
        next_run = cycle_start + timedelta(minutes=SCHEDULE_INTERVAL_MINUTES)
        now = datetime.now()

        if next_run <= now:
            print(f"\n⚠️ Cycle #{cycle} took {round(duration.total_seconds() / 60, 1)} min (exceeded {SCHEDULE_INTERVAL_MINUTES}m interval).")
            print("🚀 Starting next cycle immediately...")
        else:
            wait_seconds = (next_run - now).total_seconds()
            print(f"\n💤 Cycle #{cycle} finished in {round(duration.total_seconds() / 60, 1)} min.")
            print(f"⏳ Next run scheduled at: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (in {round(wait_seconds / 60, 1)} minute(s))")

            # Non-blocking wait loop with 30-second chunks so Ctrl+C stays responsive
            while wait_seconds > 0:
                sleep_chunk = min(wait_seconds, 30)
                await asyncio.sleep(sleep_chunk)
                wait_seconds -= sleep_chunk

        cycle += 1


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Scheduler stopped by user.")