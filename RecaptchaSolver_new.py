from DrissionPage import ChromiumPage, ChromiumOptions
from RecaptchaSolver_new import RecaptchaSolver
import time
import os
import re

# ====================== CONFIG ======================
BRAND_NAMES = [
    "Nike",
    # "Adidas",
    # "Puma",
    # add more brands here
]

SCREENSHOT_DIR = "screenshots"
MAX_CAPTCHA_ROUNDS = 6          # how many times to retry captcha per search
HEADLESS = False                # set True if you want invisible browser
# ====================================================


def create_driver():
    options = ChromiumOptions()

    # Anti-detection arguments (same idea as your Playwright version)
    for arg in [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--lang=en-US",
        "--window-size=1440,1000",
    ]:
        options.set_argument(arg)

    # Optional: force a specific Chrome path (uncomment if needed)
    # options.set_browser_path(r"C:\Users\AI ML Shalynee\AppData\Local\Google\Chrome\Application\chrome.exe")

    if HEADLESS:
        options.headless()

    return ChromiumPage(addr_or_opts=options)


def safe_filename(name: str) -> str:
    """Convert brand name to a safe Windows/Linux filename."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    print("Starting browser...")
    driver = create_driver()
    solver = RecaptchaSolver(driver)

    try:
        for brand in BRAND_NAMES:
            print(f"\n{'='*50}")
            print(f"Searching for: {brand}")
            print(f"{'='*50}")

            search_url = f"https://www.google.com/search?q={brand}"
            driver.get(search_url)

            # Give the page a moment to load
            time.sleep(2)

            # ---------- CAPTCHA HANDLING ----------
            print("Checking for captcha...")
            try:
                solver.solveCaptcha(max_rounds=MAX_CAPTCHA_ROUNDS)
                print("[+] Captcha handled (or not present)")
            except Exception as e:
                print(f"[!] Captcha solver error: {e}")
                # You can choose to continue or skip this brand
                # continue

            # Extra wait after captcha
            time.sleep(2)

            # ---------- SCREENSHOT ----------
            filename = safe_filename(brand) + ".png"
            path = os.path.join(SCREENSHOT_DIR, filename)

            driver.get_screenshot(path=path, full_page=True)
            print(f"[+] Screenshot saved → {path}")

            # Optional short pause between brands
            time.sleep(1.5)

    finally:
        print("\nClosing browser...")
        driver.quit()
        print("Done.")


if __name__ == "__main__":
    main()