import puppeteer from 'puppeteer';
import path from 'path';
import os from 'os';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Click Cloudflare Turnstile checkbox
 * @param {string} url - Target URL (default: 2captcha demo)
 */
async function clickTurnstileCheckbox(url = 'https://2captcha.com/demo/cloudflare-turnstile') {
  // ========== Browser Setup ==========
  // Common paths for Edge and Brave on Windows
  const browserPaths = [
    // Microsoft Edge
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    path.join(os.homedir(), 'AppData', 'Local', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),

    // Brave Browser
    'C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe',
    'C:\\Program Files (x86)\\BraveSoftware\\Brave-Browser\\Application\\brave.exe',
    path.join(os.homedir(), 'AppData', 'Local', 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
  ];

  let executablePath = null;
  for (const p of browserPaths) {
    try {
      const fs = await import('fs');
      if (fs.existsSync(p)) {
        executablePath = p;
        console.log(`Using browser: ${p}`);
        break;
      }
    } catch {
      // ignore
    }
  }

  if (!executablePath) {
    console.log('Neither Edge nor Brave found in common locations!');
    console.log('Please check the path manually.');
    console.log('\nTo find the correct path:');
    console.log('1. Open Edge → type edge://version in address bar');
    console.log("2. Look for 'Executable Path'");
    console.log('\nFalling back to Chromium bundled with Puppeteer...');
  }

  const launchOptions = {
    headless: false, // keep visible
    args: [
      '--disable-blink-features=AutomationControlled',
      '--no-sandbox',
      '--disable-infobars',
      '--disable-dev-shm-usage',
    ],
    defaultViewport: null,
  };

  if (executablePath) {
    launchOptions.executablePath = executablePath;
  }

  let browser;
  try {
    browser = await puppeteer.launch(launchOptions);
  } catch (e) {
    console.error('Failed to start browser:', e.message);
    return;
  }

  const page = await browser.newPage();

  // Stealth: remove webdriver flag
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'webdriver', {
      get: () => undefined,
    });
  });

  console.log(`\n${'='.repeat(60)}`);
  console.log(`Opening: ${url}`);
  console.log(`${'='.repeat(60)}\n`);

  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise((r) => setTimeout(r, 4000));

  console.log(`Page Title : ${await page.title()}`);
  console.log(`Current URL: ${page.url()}`);
  console.log('-'.repeat(50));

  // ========== Method 1: Click container ==========
  console.log('\n[Method 1] Trying to click .cf-turnstile container...');
  try {
    const widget = await page.$('.cf-turnstile');
    if (widget) {
      await widget.click();
      console.log('  ✓ Clicked .cf-turnstile');
      await new Promise((r) => setTimeout(r, 2000));
    } else {
      console.log('  ✗ .cf-turnstile not found');
    }
  } catch (e) {
    console.log(`  ✗ Failed: ${e.message}`);
  }

  // ========== Method 2: Iframe + Checkbox ==========
  console.log('\n[Method 2] Looking for Turnstile iframe...');
  try {
    const iframeSelectors = [
      'iframe[src*="challenges.cloudflare.com"]',
      'iframe[src*="turnstile"]',
      'iframe[title*="Cloudflare"]',
      'iframe[title*="Widget containing"]',
      'iframe',
    ];

    let frame = null;
    let foundSel = null;

    for (const sel of iframeSelectors) {
      try {
        const handle = await page.$(sel);
        if (handle) {
          frame = await handle.contentFrame();
          if (frame) {
            foundSel = sel;
            console.log(`  ✓ Found iframe → ${sel}`);
            break;
          }
        }
      } catch {
        // continue
      }
    }

    if (frame) {
      console.log('  Switching into iframe...');
      let clicked = false;

      const checkboxSelectors = [
        'input[type="checkbox"]',
        '.cb-i',
        '.ctp-checkbox-label',
        'label',
        'xpath//input[@type="checkbox"]',
      ];

      for (const sel of checkboxSelectors) {
        try {
          let checkbox;
          if (sel.startsWith('xpath')) {
            const xpath = sel.replace('xpath', '');
            checkbox = await frame.$x(xpath);
            checkbox = checkbox[0];
          } else {
            checkbox = await frame.$(sel);
          }

          if (checkbox) {
            console.log(`  ✓ Found checkbox → ${sel}`);
            await checkbox.click();
            console.log('  ✓ Clicked checkbox!');
            clicked = true;
            break;
          }
        } catch {
          // continue
        }
      }

      if (!clicked) {
        console.log('  ✗ No checkbox found inside iframe');
        try {
          await frame.click('body');
          console.log('  → Clicked iframe body as fallback');
        } catch {
          // ignore
        }
      }
    } else {
      console.log('  ✗ No suitable iframe found');
    }
  } catch (e) {
    console.log(`  ✗ Method 2 error: ${e.message}`);
  }

  // ========== Method 3: Coordinate click ==========
  console.log('\n[Method 3] Trying coordinate-based click...');
  try {
    let widget = await page.$('.cf-turnstile');
    if (!widget) {
      widget = await page.$('[data-sitekey]');
    }

    if (widget) {
      const box = await widget.boundingBox();
      if (box) {
        const offsetX = 28;
        const offsetY = box.height / 2;
        await page.mouse.click(box.x + offsetX, box.y + offsetY);
        console.log('  ✓ Coordinate click performed');
      }
    } else {
      console.log('  ✗ Widget not found for coordinate click');
    }
  } catch (e) {
    console.log(`  ✗ Method 3 error: ${e.message}`);
  }

  // ========== Check for token ==========
  console.log('\n[Checking] Waiting for token (up to 12 seconds)...');
  let token = null;

  for (let i = 0; i < 12; i++) {
    try {
      const tokenEl = await page.$('input[name="cf-turnstile-response"]');
      if (tokenEl) {
        const val = await page.evaluate((el) => el.value, tokenEl);
        if (val && val.length > 30) {
          token = val;
          console.log(`  ✓ SUCCESS! Token received after ${i + 1}s`);
          console.log(`  Token: ${token.slice(0, 90)}...`);
          break;
        }
      }
    } catch {
      // ignore
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  if (!token) {
    console.log('  ✗ No token generated');
  }

  console.log(`\n${'='.repeat(60)}`);
  if (token) {
    console.log('RESULT: Turnstile appears solved!');
  } else {
    console.log('RESULT: Could not solve automatically.');
  }
  console.log(`${'='.repeat(60)}\n`);

  // Wait for user to press Enter before closing
  process.stdin.setRawMode?.(true);
  console.log('Press Enter to close the browser...');
  await new Promise((resolve) => {
    process.stdin.once('data', () => {
      process.stdin.setRawMode?.(false);
      resolve();
    });
  });

  await browser.close();
}

// ========== Main ==========
const targetUrl = process.argv[2] || 'https://abuse.cloudflare.com/phishing';

clickTurnstileCheckbox(targetUrl).catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
