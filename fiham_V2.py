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
# From stealth-template.mjs / stealth_template2.py
# IIFE strips Playwright main-world artifacts on every page navigation.
# Verified to flip isPlaywright true→false on bot-detector.rebrowser.net.
# Locale is NOT set — emulating it overrides navigator.languages on the main
# thread only; workers keep the real list, causing a detectable mismatch.
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
# ==================================================================


# ====================== STEALTH HELPERS ===========================

async def human_delay(min_ms: float = 100, max_ms: float = 500) -> None:
    """Add a human-like random delay between actions (stealth_template2.py)."""
    delay = random.uniform(min_ms, max_ms)
    await asyncio.sleep(delay / 1000.0)


async def simulate_mouse_movement(page, moves: int = None) -> None:
    """
    Simulate natural mouse movement. steps=10 produces smooth interpolated
    trajectories that look organic to behavioral detectors (stealth_template2.py).
    """
    count = moves if moves is not None else (5 + math.floor(random.random() * 5))
    for _ in range(count):
        await page.mouse.move(
            100 + random.random() * 600,
            100 + random.random() * 400,
            steps=10,
        )
        await human_delay(50, 200)
# ==================================================================


class PlaywrightRecaptchaSolver:
    """reCAPTCHA solver designed natively for Playwright / rebrowser-playwright."""
    TEMP_DIR = Path(os.getenv("TEMP") or "/tmp")
    TIMEOUT = 8

    def __init__(self, page):
        self.page = page

    @property
    def anchor_frame(self):
        """The checkbox iframe"""
        return self.page.frame_locator('iframe[src*="anchor"], iframe[title="reCAPTCHA"]').first

    @property
    def challenge_frame(self):
        """The popup challenge (images / audio) iframe"""
        return self.page.frame_locator('iframe[src*="bframe"], iframe[title*="challenge" i], iframe[title*="expires in two minutes" i]').first

    async def is_captcha_present(self) -> bool:
        """Check if reCAPTCHA is currently on the page and unsolved."""
        try:
            if "sorry/index" in self.page.url:
                return True
            anchor_count = await self.page.locator('iframe[src*="anchor"], iframe[title="reCAPTCHA"]').count()
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
            # If anchor iframe is gone from a sorry/recaptcha page, it has cleared
            count = await self.page.locator('iframe[src*="anchor"], iframe[title="reCAPTCHA"]').count()
            if count == 0 and "sorry" not in self.page.url:
                return True
        except Exception:
            pass

        return False

    async def is_detected(self) -> bool:
        """Check if Google detected bot activity (Try again later)."""
        try:
            msg = self.challenge_frame.locator(".rc-doscaptcha-body-text, :text('Try again later'), :text('automated queries')")
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
            audio_src = self.challenge_frame.locator("#audio-source, a.rc-audiochallenge-tdownload-link, a[href*='audio.mp3']")
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

            await asyncio.sleep(2.0)

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
                await asyncio.sleep(2.0)
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
            audio_input_visible = await audio_input.count() > 0 and await audio_input.first.is_visible(timeout=1000)
        except Exception:
            audio_input_visible = False

        if not audio_input_visible:
            audio_btn = self.challenge_frame.locator("#recaptcha-audio-button")
            try:
                if await audio_btn.count() > 0 and await audio_btn.first.is_visible(timeout=3000):
                    print("[Captcha] Switching to audio challenge...")
                    await audio_btn.first.click(timeout=6000, force=True)
                    await asyncio.sleep(2.0)
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
                elem = self.challenge_frame.locator("a.rc-audiochallenge-tdownload-link, a[href*='audio.mp3']")
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
        await asyncio.sleep(0.5)

        verify_btn = self.challenge_frame.locator("#recaptcha-verify-button").first
        await verify_btn.click(timeout=6000, force=True)
        await asyncio.sleep(2.5)

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
                    check=True
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
NUM_BROWSERS = 1                # Number of Chrome windows to open
TABS_PER_BROWSER = 20           # Max active tabs per Chrome window
TAB_STAGGER_DELAY = 1.0         # Delay between opening each new tab (in seconds)
MAX_TAB_RETRIES = 5             # Max fresh tab attempts if captcha fails

# --- Scheduler Settings ---
SCHEDULE_INTERVAL_MINUTES = 45  # Interval in minutes (e.g. 60 = every 1 hour, 30 = every 30 mins)
ENABLE_SCHEDULER = True         # Set to True to repeat automatically, False for single execution
# ================================================================

# Function to dynamically load brands
def load_brands():
    candidates = [
        Path(r".\brand.json"),
        Path(r"C:\Users\AI Fiham\Downloads\Telegram Desktop\brand.json"),
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

# Directories
screenshots_dir = Path("screenshots")
screenshots_dir.mkdir(exist_ok=True)

json_dir = Path("results_json")
json_dir.mkdir(exist_ok=True)

txt_dir = Path("results_txt")
txt_dir.mkdir(exist_ok=True)

results_file = Path("search_results.txt")
master_json_file = Path("search_results.json")
history_json_file = Path("all_search_results_history.json")

# HTTP session
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
})

file_lock = threading.Lock()
completed_counter = {"count": 0}
counter_lock = threading.Lock()

# Async lock to ensure 1-second gap between opening tabs
launch_lock = asyncio.Lock()


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


async def human_type(page, selector: str, text: str) -> None:
    """
    Type text with human-like per-character speed using keyboard.type.
    From stealth-template.mjs / stealth_template2.py.
    """
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await human_delay(50, 150)


async def search_brand_single_tab(context, brand, index=1, total=1, attempt=1, cycle_info=None) -> bool:
    """Perform a search for a brand in a single tab. Returns True on success, False if captcha/detection blocked."""
    # Enforce 1-second stagger delay before opening the new tab
    async with launch_lock:
        page = await context.new_page()
        if attempt > 1:
            print(f"[{index}/{total}] Opened retry tab #{attempt} for: {brand} (waiting {TAB_STAGGER_DELAY}s before next tab launch)")
        else:
            print(f"[{index}/{total}] Opened tab for: {brand} (waiting {TAB_STAGGER_DELAY}s before next tab launch)")
        await asyncio.sleep(TAB_STAGGER_DELAY)

    try:
        await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=45000)
        # page.wait_for_timeout for in-browser timing (search_and_scrape2.py pattern)
        await page.wait_for_timeout(random.randint(1800, 3200))

        # Check for initial captcha
        solver = PlaywrightRecaptchaSolver(page)
        if await solver.is_captcha_present():
            print(f"[{index}/{total}] Captcha detected before search for {brand} — solving...")
            solved = await solver.solve(max_rounds=6)
            if not solved:
                print(f"[{index}/{total}] Captcha could not be solved before search for {brand}")
                return False

        # Human-like mouse movement: multi-step smooth trajectory (stealth_template2.py)
        await simulate_mouse_movement(page)
        await page.wait_for_timeout(random.randint(400, 900))

        await human_type(page, "textarea[name='q']", brand)
        await page.keyboard.press("Enter")

        await page.wait_for_load_state("domcontentloaded")
        # Scroll a bit like a human reading results (search_and_scrape2.py)
        await page.evaluate("""() => {
            window.scrollBy(0, Math.floor(Math.random() * 600) + 200);
        }""")
        await page.wait_for_timeout(random.randint(2500, 4500))
        await simulate_mouse_movement(page, moves=random.randint(3, 6))

        # ---------- CAPTCHA HANDLING ----------
        if await solver.is_captcha_present():
            print(f"[{index}/{total}] Captcha detected for {brand} — solving...")
            success = await solver.solve(max_rounds=6)
            if not success:
                print(f"[{index}/{total}] Could not solve captcha for {brand}")
                return False
            await asyncio.sleep(2)
            await page.wait_for_load_state("domcontentloaded")
        # --------------------------------------

        # Verify page is not still blocked by captcha
        if await solver.is_captcha_present() or "sorry/index" in page.url:
            print(f"[{index}/{total}] Captcha still blocking results for {brand}")
            return False

        # Extract results
        results = page.locator("a h3")
        count = await results.count()
        print(f"[{index}/{total}] {brand}: Found {count} results")

        # If 0 results because of bot detection or sorry page, treat as failed tab to retry
        if count == 0 and (await solver.is_detected() or "sorry" in page.url):
            print(f"[{index}/{total}] Bot detection triggered on '{brand}'")
            return False

        search_url = page.url

        # Determine target cycle paths (preserves every run without replacing)
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

        safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in str(brand)).strip().replace(" ", "_")
        screenshot_path = curr_screenshots_dir / f"{safe_name}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"[{index}/{total}] {brand}: Screenshot saved → {screenshot_path}")

        with counter_lock:
            completed_counter["count"] += 1
            done = completed_counter["count"]
        balance = total - done

        # Collect results for JSON & TXT
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
                    print(f"[{index}/{total}] {brand}: {i+1}. {title}")
                    print(f"         → {link}")
                    brand_results.append({
                        "rank": i + 1,
                        "title": title,
                        "url": link
                    })
            except Exception:
                continue

        # ---------- JSON OUTPUT ----------
        brand_data = {
            "cycle": cycle_number,
            "timestamp": timestamp_str,
            "index": index,
            "total": total,
            "brand": brand,
            "status": {
                "completed": done,
                "balance": balance,
                "total": total
            },
            "search_url": search_url,
            "screenshot": str(screenshot_path),
            "results_count": count,
            "results": brand_results
        }

        # 1. Individual brand JSON for this cycle
        brand_json_path = curr_json_dir / f"{safe_name}.json"
        with open(brand_json_path, "w", encoding="utf-8") as f:
            json.dump(brand_data, f, ensure_ascii=False, indent=2)
        print(f"[{index}/{total}] JSON saved → {brand_json_path}")

        # 2. Append to Cycle Master JSON & Global History JSON (thread-safe)
        with file_lock:
            # Cycle Master JSON
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

            # Global Cumulative History JSON
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
        # ---------------------------------

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

        print(f"✅ [{index}/{total}] {brand} done — Completed: {done} | Balance: {balance} | Total: {total}\n")
        return True

    except Exception as err:
        print(f"[!] [{index}/{total}] Error searching '{brand}' on tab #{attempt}: {err}")
        return False
    finally:
        await page.close()


async def search_brand(context, brand, index=1, total=1, cycle_info=None):
    """Search brand with automatic fresh-tab retries until good results are obtained."""
    for attempt in range(1, MAX_TAB_RETRIES + 1):
        print(f"\n{'='*60}")
        if attempt > 1:
            print(f"[{index}/{total}] Retrying brand: {brand} (Attempt {attempt}/{MAX_TAB_RETRIES}) in fresh tab")
        else:
            print(f"[{index}/{total}] Preparing to search: {brand}")
        print(f"{'='*60}")

        success = await search_brand_single_tab(context, brand, index, total, attempt=attempt, cycle_info=cycle_info)
        if success:
            return True

        if attempt < MAX_TAB_RETRIES:
            print(f"[!] [{index}/{total}] Captcha/Search issue for '{brand}'. Discarding tab and retrying in a fresh tab...")
            await asyncio.sleep(random.uniform(1.0, 2.0))

    print(f"❌ [{index}/{total}] Exhausted {MAX_TAB_RETRIES} attempts for '{brand}'.")
    return False


async def run_single_cycle(cycle_num=1):
    brands = load_brands()
    total_brands = len(brands)

    # Reset completed counter for this cycle
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

    total_parallel = NUM_BROWSERS * TABS_PER_BROWSER
    print(f"\n{'='*60}")
    print(f"Cycle #{cycle_num} | Time: {readable_time}")
    print(f"Total brands loaded: {total_brands}")
    print(f"Cycle outputs folder: {cycle_tag}")
    print(f"Running {NUM_BROWSERS} Chrome window(s) × {TABS_PER_BROWSER} tabs each = {total_parallel} max parallel searches")
    print(f"Stagger delay between tab openings: {TAB_STAGGER_DELAY} second(s)")
    print(f"Max retries per brand if captcha fails: {MAX_TAB_RETRIES} fresh tab(s)")
    print(f"{'='*60}\n")

    if total_brands == 0:
        print("[!] No brands found to search. Skipping cycle.")
        return

    # Shared brand queue across all browser windows
    brand_queue = asyncio.Queue()
    for index, brand in enumerate(brands, 1):
        await brand_queue.put((index, brand))

    async def run_browser_window(playwright_instance, browser_id):
        """Launch one Chrome window and run TABS_PER_BROWSER workers in it."""
        browser = await playwright_instance.chromium.launch(
            headless=False,
            channel="chrome",
            # ── stealth-template.mjs: drop --enable-automation via ignore_default_args ──
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--start-maximized",
            ],
        )

        # ── stealth-template.mjs: locale intentionally omitted ──
        # Emulating a locale overrides navigator.languages on the MAIN thread
        # only; workers keep Chrome's real list — a mismatch detectors flag.
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            timezone_id="Asia/Kolkata",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

        # ── stealth_template2.py: inject IIFE artifact-stripping script on every nav ──
        await context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS_JS)

        print(f"🌐 Browser #{browser_id} launched with up to {TABS_PER_BROWSER} parallel tabs")

        async def worker(worker_id):
            while not brand_queue.empty():
                try:
                    index, brand = brand_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    await search_brand(context, brand, index, total_brands, cycle_info=cycle_info)
                finally:
                    brand_queue.task_done()

        remaining = brand_queue.qsize()
        num_workers = min(TABS_PER_BROWSER, remaining)
        workers = [
            asyncio.create_task(worker(w_id))
            for w_id in range(num_workers)
        ]

        await asyncio.gather(*workers)
        await browser.close()
        print(f"🌐 Browser #{browser_id} finished and closed")

    async with async_playwright() as p:
        # Launch all browser windows concurrently
        browser_tasks = [
            asyncio.create_task(run_browser_window(p, bid + 1))
            for bid in range(NUM_BROWSERS)
        ]
        await asyncio.gather(*browser_tasks)

    print(f"\n✅ Cycle #{cycle_num} completed! All {total_brands} brand searches finished.")


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

            # Non-blocking wait loop with periodic check
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