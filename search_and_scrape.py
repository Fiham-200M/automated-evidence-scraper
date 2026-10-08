#!/usr/bin/env python3
# before running the script install python dependencies:
# pip install playwright
# playwright install chrome
# Also install: pip install SpeechRecognition pydub
# (and have ffmpeg available on PATH)

import os
import sys
import json
import time
import random
import shutil
import asyncio
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urljoin

# rebrowser's Runtime.enable fix must be set BEFORE importing the library
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

from playwright.async_api import async_playwright, Page, BrowserContext, FrameLocator, Locator

# Import from local stealth template
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
try:
    from stealth_template import simulate_mouse_movement
except ImportError:
    # Fallback if scripts/ layout differs
    from stealth_template import simulate_mouse_movement

# ====================== CONFIGURATION ======================
TERMS_FILE = "./brand.json"
OUTPUT_DIR = "./output"
SEARCH_ENGINE = "https://www.google.com/search?udm=14&hl=id&gl=id&q="  # udm=14 forces clean Web results
MIN_DELAY_MS = 4000   # delay between searches
MAX_DELAY_MS = 9000
CONCURRENT_TABS = 54        # number of parallel tabs to open
TAB_OPEN_DELAY_SEC = 1.0   # stagger gap between opening each tab (seconds)

# ---- Chrome real browser paths ----
# The executable used to launch the browser
REAL_CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    r"C:\Users\AI ML Shalynee\AppData\Local\Google\Chrome\Application\chrome.exe"
)
# The User Data directory that holds your logged-in profiles / cookies
CHROME_USER_DATA_DIR = os.environ.get(
    "CHROME_USER_DATA",
    r"C:\Users\AI ML Shalynee\AppData\Local\Google\Chrome\User Data"
)
# Which profile subfolder to use (Default = main profile)
CHROME_PROFILE = os.environ.get("CHROME_PROFILE", "Default")

# ---- Scheduler Settings ----
SCHEDULE_INTERVAL_MINUTES = 20  # interval between cycles (minutes)
ENABLE_SCHEDULER = True         # True = repeat automatically; False = single run

# ====================== FFMPEG CONFIGURATION ======================
def setup_ffmpeg() -> str:
    bin_dir = Path("bin").resolve()
    ffmpeg_exe = bin_dir / "ffmpeg.exe"
    if ffmpeg_exe.exists():
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        return str(ffmpeg_exe)
    return "ffmpeg"


# ====================== AUDIO CHALLENGE TRANSCRIBER ======================
async def process_audio_challenge(audio_url: str) -> str:
    temp_dir = tempfile.gettempdir()
    rand_id = random.randint(10000, 99999)
    mp3_path = Path(temp_dir) / f"rc_{rand_id}.mp3"
    wav_path = Path(temp_dir) / f"rc_{rand_id}.wav"
    ffmpeg_cmd = setup_ffmpeg()

    try:
        # 1. Download MP3 audio challenge
        import urllib.request
        req = urllib.request.Request(
            audio_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Audio download failed: {resp.reason}")
            data = resp.read()
            mp3_path.write_bytes(data)

        # 2. Convert MP3 to 16kHz mono WAV using ffmpeg
        subprocess.run(
            [ffmpeg_cmd, "-y", "-i", str(mp3_path), "-ar", "16000", "-ac", "1", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        # 3. Speech Recognition using Python speech_recognition
        try:
            import speech_recognition as sr
            r = sr.Recognizer()
            with sr.AudioFile(str(wav_path)) as src:
                audio = r.record(src)
            return r.recognize_google(audio)
        except Exception as py_err:
            # Direct Google Speech API fallback if python speech_recognition encounters an issue
            wav_data = wav_path.read_bytes()
            api_url = "http://www.google.com/speech-api/v2/recognize?client=chromium&lang=en-US&key=AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"
            api_req = urllib.request.Request(
                api_url,
                data=wav_data,
                headers={"Content-Type": "audio/l16; rate=16000"},
                method="POST",
            )
            with urllib.request.urlopen(api_req) as api_resp:
                api_text = api_resp.read().decode("utf-8")
            for line in api_text.split("\n"):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                    if (
                        parsed.get("result")
                        and parsed["result"][0].get("alternative")
                        and parsed["result"][0]["alternative"][0].get("transcript")
                    ):
                        return parsed["result"][0]["alternative"][0]["transcript"]
                except Exception:
                    pass
            raise py_err
    finally:
        for p in (mp3_path, wav_path):
            try:
                p.unlink()
            except Exception:
                pass


# ====================== RECAPTCHA SOLVER ======================
class PlaywrightRecaptchaSolver:
    """
    reCAPTCHA solver designed natively for Playwright.
    """

    def __init__(self, page: Page):
        self.page = page

    @property
    def anchor_frame(self) -> FrameLocator:
        # The checkbox iframe
        return self.page.frame_locator('iframe[src*="anchor"], iframe[title="reCAPTCHA"]').first

    @property
    def challenge_frame(self) -> FrameLocator:
        # The popup challenge (images / audio) iframe
        return self.page.frame_locator(
            'iframe[src*="bframe"], iframe[title*="challenge" i], iframe[title*="expires in two minutes" i]'
        ).first

    async def is_captcha_present(self) -> bool:
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
        try:
            anchor = self.anchor_frame.locator("#recaptcha-anchor")
            if await anchor.count() > 0:
                checked = await anchor.get_attribute("aria-checked", timeout=1000)
                if checked == "true":
                    return True
        except Exception:
            pass

        try:
            # If anchor iframe is gone from a sorry/recaptcha page, it has cleared
            count = await self.page.locator(
                'iframe[src*="anchor"], iframe[title="reCAPTCHA"]'
            ).count()
            if count == 0 and "sorry" not in self.page.url:
                return True
        except Exception:
            pass

        return False

    async def is_detected(self) -> bool:
        try:
            msg = self.challenge_frame.locator(
                '.rc-doscaptcha-body-text, :text("Try again later"), :text("automated queries")'
            )
            if await msg.count() > 0 and await msg.first.is_visible(timeout=500):
                return True
        except Exception:
            pass

        try:
            page_msg = self.page.locator(':text("Try again later")')
            if await page_msg.first.is_visible(timeout=500):
                return True
        except Exception:
            pass

        return False

    async def is_challenge_open(self) -> bool:
        try:
            audio_btn = self.challenge_frame.locator("#recaptcha-audio-button")
            audio_src = self.challenge_frame.locator(
                '#audio-source, a.rc-audiochallenge-tdownload-link, a[href*="audio.mp3"]'
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
            except Exception as err:
                print(f"[Captcha] Round {round_num} note: {err}")

            await self.page.wait_for_timeout(2000)

            if not await self.is_captcha_present() or await self.is_solved():
                print("[Captcha] Solved successfully!")
                return True

        print("[Captcha] Failed to solve after max rounds")
        return False

    async def _solve_single(self) -> None:
        # 1. If challenge popup is not open, click checkbox
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
            raise RuntimeError("Bot detected by Google (Try again later)")

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
            raise RuntimeError("Bot detected by Google (Try again later)")

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
                    'a.rc-audiochallenge-tdownload-link, a[href*="audio.mp3"]'
                )
                if await elem.count() > 0:
                    audio_url = await elem.first.get_attribute("href", timeout=4000)
            except Exception:
                pass

        if not audio_url:
            if await self.is_detected():
                raise RuntimeError("Bot detected by Google (Try again later)")
            raise RuntimeError("Could not find audio source URL")

        print("[Captcha] Processing audio challenge...")

        # 5. Download and transcribe speech
        text = await process_audio_challenge(audio_url)
        print(f"[Captcha] Recognized text: {text}")

        if not text or not text.strip():
            raise RuntimeError("Speech transcription returned empty text")

        # 6. Fill audio response and click Verify
        input_field = self.challenge_frame.locator("#audio-response").first
        await input_field.fill(text.strip().lower())
        await self.page.wait_for_timeout(500)

        verify_btn = self.challenge_frame.locator("#recaptcha-verify-button").first
        await verify_btn.click(timeout=6000, force=True)
        await self.page.wait_for_timeout(2500)


# ====================== HUMANLIKE BEHAVIOR HELPERS ======================
def random_between(min_val: int, max_val: int) -> int:
    return random.randint(min_val, max_val)


def random_delay(min_ms: int = MIN_DELAY_MS, max_ms: int = MAX_DELAY_MS) -> int:
    return random_between(min_ms, max_ms)


async def human_type(page: Page, selector: str, text: str) -> None:
    """
    Type text character-by-character with human-like timing and micro-pauses.
    """
    element = page.locator(selector)
    await element.click()
    await page.wait_for_timeout(random_between(300, 700))

    for char in text:
        await element.type(char, delay=random_between(70, 180))
        if random.random() < 0.12:
            await page.wait_for_timeout(random_between(150, 400))

    await page.wait_for_timeout(random_between(400, 900))


# ====================== LINK RESOLUTION HELPER ======================
async def resolve_redirect_url(raw_url: Optional[str], page: Optional[Page] = None) -> Optional[str]:
    """
    Resolves Google search redirect/proxy links to the real destination URL.
    """
    if not raw_url:
        return raw_url

    try:
        parsed = urlparse(raw_url)

        # 1. Handle plain /url?q=... or /url?url=...
        qs = parse_qs(parsed.query)
        if "q" in qs:
            target = qs["q"][0]
            if target.lower().startswith(("http://", "https://")):
                return target

        if "url" in qs:
            target = qs["url"][0]
            if target.lower().startswith(("http://", "https://")):
                return target

        # 2. Handle encrypted /goto?url=... or /url?... Google proxy links
        if "google." in parsed.hostname and (
            "/goto" in parsed.path or "/url" in parsed.path
        ):
            import urllib.request
            req = urllib.request.Request(
                raw_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                },
                method="GET",
            )
            # Use a custom opener that does not follow redirects
            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            opener = urllib.request.build_opener(NoRedirectHandler)
            try:
                with opener.open(req) as resp:
                    location = resp.headers.get("Location")
                    if location:
                        if location.startswith("/"):
                            return f"https://www.google.com{location}"
                        return location
            except Exception:
                pass

            if page:
                try:
                    response = await page.request.get(raw_url, max_redirects=5, timeout=4000)
                    final_url = response.url
                    if (
                        final_url
                        and "google.com/goto" not in final_url
                        and "google.com/url" not in final_url
                    ):
                        return final_url
                except Exception:
                    pass
    except Exception:
        # Fallback to raw_url if network lookup fails
        pass

    return raw_url


async def ensure_dir(dir_path: str) -> None:
    Path(dir_path).mkdir(parents=True, exist_ok=True)


# ====================== CACHE CLEARING ======================
async def clear_browser_cache(page: Page) -> None:
    """
    Clears browser HTTP cache (and optionally cookies) before each cycle.
    Keeps login cookies by default so the session still looks like a real user.
    """
    try:
        client = await page.context.new_cdp_session(page)
        await client.send("Network.clearBrowserCache")
        # Uncomment the next line if you also want to clear cookies every cycle
        # await client.send("Network.clearBrowserCookies")
        print("[Cache] Browser cache cleared")
    except Exception as err:
        print(f"[Cache] Could not clear cache: {err}")


# ====================== RESULTS SAVER ======================
async def save_results(
    term: str,
    page: Page,
    index: int,
    total: int,
    brand_results: List[Dict[str, Any]],
    count: int,
    search_url: str,
    cycle_output_dir: str,
) -> None:
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in term)[:60]
    base = str(Path(cycle_output_dir) / f"{str(index).zfill(3)}_{safe_name}")
    screenshot_path = f"{base}.png"

    # 1. Screenshot
    await page.screenshot(path=screenshot_path, full_page=True)

    # 2. Text content (readable)
    text = await page.evaluate("() => document.body.innerText")
    Path(f"{base}.txt").write_text(text, encoding="utf-8")

    # 3. Full HTML (for archiving / parsing)
    html = await page.content()
    Path(f"{base}.html").write_text(html, encoding="utf-8")

    # 4. Individual Brand JSON
    brand_data = {
        "index": index,
        "total": total,
        "brand": term,
        "search_url": search_url,
        "screenshot": screenshot_path,
        "results_count": count,
        "results": brand_results,
    }
    Path(f"{base}.json").write_text(json.dumps(brand_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. Append to Cycle Master JSON (lives inside the stamped folder)
    cycle_master_json = Path(cycle_output_dir) / "search_results.json"
    master_data: List[Dict[str, Any]] = []
    if cycle_master_json.exists():
        try:
            master_data = json.loads(cycle_master_json.read_text(encoding="utf-8"))
            if not isinstance(master_data, list):
                master_data = []
        except Exception:
            master_data = []
    master_data.append(brand_data)
    cycle_master_json.write_text(json.dumps(master_data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  → saved {base}.{{png,txt,html,json}} ({len(brand_results)} organic links saved)")


# ====================== SEARCH SINGLE BRAND ======================
async def search_brand(
    page: Page,
    brand: str,
    index: int,
    total: int,
    cycle_output_dir: str,
) -> bool:
    print("\n" + "=" * 60)
    print(f'[{index}/{total}] Searching brand: "{brand}"')
    print("=" * 60)

    solver = PlaywrightRecaptchaSolver(page)

    try:
        # 1. Navigate to Google homepage first with natural human behavior
        await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(random_between(1800, 3200))

        # Check for initial captcha
        if await solver.is_captcha_present():
            print(f"[{index}/{total}] Captcha detected on Google home for {brand} — solving...")
            solved = await solver.solve(6)
            if not solved:
                print(f"[{index}/{total}] Captcha could not be solved before search for {brand}")
                return False

        # Human-like mouse movement
        await page.mouse.move(random_between(200, 900), random_between(150, 450), steps=5)
        await page.wait_for_timeout(random_between(400, 900))
        await simulate_mouse_movement(page)

        # 2. Navigate to search results with udm=14
        search_url = f"{SEARCH_ENGINE}{brand}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(random_between(2500, 4500))

        # 3. Captcha check on search results
        if await solver.is_captcha_present():
            print(f"[{index}/{total}] Captcha detected on search results for {brand} — solving...")
            solved = await solver.solve(6)
            if not solved:
                print(f"[{index}/{total}] Could not solve captcha for {brand}")
                return False
            await page.wait_for_timeout(2000)
            await page.wait_for_load_state("domcontentloaded")

        # Verify page is not still blocked by captcha
        if await solver.is_captcha_present() or "sorry/index" in page.url:
            print(f"[{index}/{total}] Captcha still blocking results for {brand}")
            return False

        # 4. Human-like scrolling
        await page.evaluate(
            "() => { window.scrollBy(0, Math.floor(Math.random() * 600) + 200); }"
        )
        await page.wait_for_timeout(random_between(600, 1500))

        # 5. Extract results (headings and links)
        results = page.locator("a h3")
        count = await results.count()

        # Fallback for localized or layout variants
        if count == 0:
            alt_headings = page.locator("#rso h3, #search h3, div.g h3, .MjjYud h3")
            count = await alt_headings.count()

        print(f"[{index}/{total}] {brand}: Found {count} results")

        if count == 0 and (await solver.is_detected() or "sorry" in page.url):
            print(f"[{index}/{total}] Bot detection triggered on '{brand}'")
            return False

        # Collect and resolve top organic links
        brand_results: List[Dict[str, Any]] = []
        seen_urls = set()

        for i in range(min(10, count)):
            try:
                heading = results.nth(i)
                title = (await heading.inner_text()).strip()
                link = await heading.locator("xpath=..").get_attribute("href")

                # Fallback: check if anchor is an ancestor
                if not link:
                    link = await heading.evaluate(
                        "el => el.closest('a')?.getAttribute('href') || null"
                    )

                if link and link.startswith("/"):
                    link = f"https://www.google.com{link}"

                link = await resolve_redirect_url(link, page)

                if title and link and link not in seen_urls:
                    seen_urls.add(link)
                    print(f"[{index}/{total}] {brand}: {len(brand_results) + 1}. {title}")
                    print(f"         → {link}")
                    brand_results.append({
                        "rank": len(brand_results) + 1,
                        "title": title,
                        "url": link,
                    })
            except Exception:
                continue

        # Save results to disk
        await save_results(
            brand, page, index, total, brand_results, count, page.url, cycle_output_dir
        )
        print(f"✅ [{index}/{total}] {brand} finished successfully.\n")
        return True

    except Exception as err:
        print(f"[!] [{index}/{total}] Error searching '{brand}': {err}")
        return False


# ====================== LOAD BRANDS ======================
async def load_brands() -> List[str]:
    candidates = [TERMS_FILE, "./terms.json"]
    for p in candidates:
        path = Path(p)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return parsed
                if parsed and isinstance(parsed.get("brands"), list):
                    return parsed["brands"]
            except Exception as err:
                print(f"[!] Error loading brands from {p}: {err}")
    raise RuntimeError(f"Could not find valid brand list in {TERMS_FILE}")


# ====================== INIT SCRIPT (strips Playwright artifacts) ======================
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
  if (!window.chrome) window.chrome = {};
}
"""


# ====================== LAUNCH REAL CHROME WITH EXISTING PROFILE ======================
"""
Chrome blocks Playwright remote-debugging (CDP) when pointed at the real system
User Data dir. Work-around: copy the essential login/cookie files from the real
Default profile into a local ./chrome-session/ folder, then launch from there.

The copy is refreshed on every call so your latest cookies are always used.
IMPORTANT: Chrome must be fully closed before running this script, otherwise the
Cookies file is locked and the copy will fail.
"""

# Files copied from the Default profile to keep login sessions / cookies alive
PROFILE_FILES_TO_COPY = [
    "Cookies",
    "Cookies-journal",
    "Extension Cookies",
    "Extension Cookies-journal",
    "Login Data",
    "Login Data-journal",
    "Login Data For Account",
    "Login Data For Account-journal",
    "Preferences",
    "Secure Preferences",
    "Web Data",
    "Web Data-journal",
    "Bookmarks",
    "Favicons",
]


async def prepare_profile_copy() -> str:
    src_dir = Path(CHROME_USER_DATA_DIR) / CHROME_PROFILE
    session_dir = Path("chrome-session").resolve()
    dest_dir = session_dir / CHROME_PROFILE

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy top-level Local State (Chrome needs this for encryption keys)
    local_state_src = Path(CHROME_USER_DATA_DIR) / "Local State"
    local_state_dst = session_dir / "Local State"
    if local_state_src.exists():
        try:
            shutil.copy2(local_state_src, local_state_dst)
        except Exception as e:
            print(f"[Profile] Could not copy Local State: {e}")

    # Copy profile-level files
    copied = 0
    for file_name in PROFILE_FILES_TO_COPY:
        src = src_dir / file_name
        dst = dest_dir / file_name
        if src.exists():
            try:
                shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                # Cookies file will be locked if Chrome is still open
                if getattr(e, "errno", None) in (11, 13) or "Permission" in str(e) or "locked" in str(e).lower():
                    print(f"\n[Profile] ⚠️  Cannot copy '{file_name}' — Chrome is still running!")
                    print("[Profile]    Please CLOSE Chrome completely and re-run the script.\n")
                else:
                    print(f"[Profile] Could not copy '{file_name}': {e}")

    print(f"[Profile] Copied {copied} session files → ./chrome-session/")
    return str(session_dir)


async def launch_with_profile() -> Tuple[BrowserContext, Page]:
    print(f"[Browser] Preparing profile copy from: {CHROME_USER_DATA_DIR}/{CHROME_PROFILE}")

    # Copy real Chrome profile to local dir (bypasses the CDP restriction on system dir)
    session_dir = await prepare_profile_copy()

    executable_path = REAL_CHROME_PATH if Path(REAL_CHROME_PATH).exists() else None
    if executable_path:
        print(f"[Browser] Executable: {executable_path}")
    print(f"[Browser] Session dir: {session_dir}")

    playwright = await async_playwright().start()

    context_options: Dict[str, Any] = {
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--profile-directory={CHROME_PROFILE}",
        ],
        "viewport": {"width": 1366, "height": 768},
        "ignore_default_args": ["--enable-automation"],
        "locale": "id-ID",
        "timezone_id": "Asia/Jakarta",
        "user_data_dir": session_dir,
    }

    if executable_path:
        context_options["executable_path"] = executable_path

    # launch_persistent_context is the equivalent of chromium.launchPersistentContext
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=session_dir,
        headless=False,
        args=context_options["args"],
        viewport=context_options["viewport"],
        ignore_default_args=context_options["ignore_default_args"],
        locale=context_options["locale"],
        timezone_id=context_options["timezone_id"],
        executable_path=executable_path,
    )

    # Strip Playwright main-world artifacts on every page navigation
    await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)

    # Reuse the first open page or open a fresh one
    pages = context.pages
    page = pages[0] if pages else await context.new_page()

    return context, page


# ====================== PARALLEL TAB WORKER ======================
async def tab_worker(
    tab_id: int,
    context: BrowserContext,
    initial_page: Optional[Page],
    queue: asyncio.Queue,
    total_terms: int,
    cycle_output_dir: str,
) -> None:
    """
    Worker coroutine: each instance pulls brands from the shared queue and
    runs search_brand on its own tab.  Tab 1 reuses the already-open page;
    all others open a fresh tab.  Tabs are staggered by TAB_OPEN_DELAY_SEC
    so they don't all hit the browser at the same instant.
    """
    if tab_id > 1:
        await asyncio.sleep((tab_id - 1) * TAB_OPEN_DELAY_SEC)

    if queue.empty():
        return

    if tab_id == 1 and initial_page is not None:
        page = initial_page
    else:
        page = await context.new_page()
        # Apply the same stealth init script to newly opened tabs
        await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS)

    print(f"[Tab {tab_id}] Tab opened and ready.")

    try:
        while not queue.empty():
            try:
                index, brand = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                success = await search_brand(
                    page, brand, index, total_terms, cycle_output_dir
                )
                if not success:
                    print(f"[Tab {tab_id}] ⚠️  '{brand}' failed — continuing with next brand.")
            except Exception as e:
                print(f"[Tab {tab_id}] Error while processing '{brand}': {e}")
            finally:
                queue.task_done()

            # Natural random delay before picking up the next brand
            if not queue.empty():
                delay = random_delay()
                print(f"[Tab {tab_id}] waiting {round(delay / 1000)}s before next search…")
                await page.wait_for_timeout(delay)

    finally:
        try:
            await page.close()
        except Exception:
            pass


# ====================== RUN ONE FULL CYCLE ======================
async def run_single_cycle(cycle_num: int = 1) -> None:
    brands = await load_brands()
    if not brands:
        print("[!] No brands found to search. Skipping cycle.")
        return

    await ensure_dir(OUTPUT_DIR)

    now = datetime.now()
    readable = now.strftime("%d/%m/%Y %H.%M.%S")  # approximate id-ID style

    # Create unique timestamped folder for this cycle (prevents overwriting)
    ts = now.strftime("%Y%m%d_%H%M%S")
    cycle_tag = f"cycle_{str(cycle_num).zfill(3)}_{ts}"
    cycle_output_dir = str(Path(OUTPUT_DIR) / cycle_tag)
    await ensure_dir(cycle_output_dir)

    total_brands = len(brands)
    num_tabs = min(CONCURRENT_TABS, total_brands)

    print("\n" + "=" * 62)
    print(f"🚀 [CYCLE #{cycle_num}] Started at: {readable}")
    print(f"   Total brands : {total_brands}")
    print(f"   Parallel tabs: {num_tabs} (staggered by {TAB_OPEN_DELAY_SEC}s)")
    print(f"   Output folder: {cycle_output_dir}")
    print("=" * 62 + "\n")

    context, page = await launch_with_profile()

    # Clear cache before starting the searches of this cycle
    await clear_browser_cache(page)

    # Populate the shared queue
    queue: asyncio.Queue = asyncio.Queue()
    for i, raw_brand in enumerate(brands):
        brand = str(raw_brand).strip()
        if brand:
            queue.put_nowait((i + 1, brand))

    if queue.empty():
        print("[!] No valid brands to search.")
        await context.close()
        return

    try:
        # Launch N parallel tab workers
        tasks = [
            asyncio.create_task(
                tab_worker(
                    tab_id=t + 1,
                    context=context,
                    initial_page=(page if t == 0 else None),
                    queue=queue,
                    total_terms=queue.qsize(),
                    cycle_output_dir=cycle_output_dir,
                )
            )
            for t in range(num_tabs)
        ]

        await asyncio.gather(*tasks)
        print(f"\n✅ Cycle #{cycle_num} done. Results saved to {cycle_output_dir}")
    finally:
        try:
            await context.close()
        except Exception:
            pass


# ====================== MAIN — SCHEDULER LOOP ======================
async def main() -> None:
    if not ENABLE_SCHEDULER:
        print("▶  Scheduler disabled. Running single execution…")
        await run_single_cycle(1)
        return

    interval_ms = SCHEDULE_INTERVAL_MINUTES * 60 * 1000
    print("\n" + "#" * 62)
    print("  ⏰  REPEATING SCHEDULER ACTIVE")
    print(f"  ⏰  Interval: {SCHEDULE_INTERVAL_MINUTES} minute(s)")
    print("  ⏰  Press Ctrl+C to stop")
    print("#" * 62 + "\n")

    cycle = 1
    while True:
        cycle_start = time.time() * 1000  # ms

        try:
            await run_single_cycle(cycle)
        except Exception as err:
            print(f"[!] Error during Cycle #{cycle}: {err}")

        elapsed = (time.time() * 1000) - cycle_start
        wait_ms = interval_ms - elapsed

        if wait_ms <= 0:
            print(
                f"\n⚠️  Cycle #{cycle} took {elapsed / 60000:.1f} min (exceeded interval). Starting next immediately…"
            )
        else:
            next_at = datetime.fromtimestamp((time.time() * 1000 + wait_ms) / 1000).strftime("%H:%M:%S")
            print(f"\n💤  Cycle #{cycle} done in {elapsed / 60000:.1f} min.")
            print(f"⏳  Next cycle at: {next_at} (in {wait_ms / 60000:.1f} min)")
            # Sleep in 30-second chunks so Ctrl+C is always responsive
            remaining = wait_ms
            while remaining > 0:
                await asyncio.sleep(min(remaining, 30_000) / 1000.0)
                remaining -= 30_000

        cycle += 1


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as err:
        print(f"[!] Fatal execution error: {err}")
        sys.exit(1)
