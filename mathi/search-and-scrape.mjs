// before running the script install node.js and use the following command to run the rebrowser-playwright
// npm install rebrowser-playwright
// node search-and-scrape.mjs

import fs from 'fs/promises';
import path from 'path';
import { createStealthBrowser, humanType, simulateMouseMovement } from './stealth-template.mjs';

// ---------- config ----------
const TERMS_FILE = './brand.json';
const OUTPUT_DIR = './output';
const SEARCH_ENGINE = 'https://www.google.com/search?q='; // change if needed
const MIN_DELAY_MS = 4000;   // between searches
const MAX_DELAY_MS = 9000;
const TYPING_DELAY = 80;     // ms per key (humanType will vary it)


// ---------- helpers ----------
function randomDelay(min = MIN_DELAY_MS, max = MAX_DELAY_MS) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

async function ensureDir(dir) {
    await fs.mkdir(dir, { recursive: true });
}

async function saveResults(term, page, index) {
    const safeName = term.replace(/[^a-z0-9]/gi, '_').slice(0, 60);
    const base = path.join(OUTPUT_DIR, `${String(index).padStart(3, '0')}_${safeName}`);

    // Screenshot
    await page.screenshot({
        path: `${base}.png`,
        fullPage: true,
    });

    // Text content (readable)
    const text = await page.evaluate(() => document.body.innerText);
    await fs.writeFile(`${base}.txt`, text, 'utf8');

    // Full HTML (for later parsing)
    const html = await page.content();
    await fs.writeFile(`${base}.html`, html, 'utf8');

    console.log(`  → saved ${base}.{png,txt,html}`);
}

// ---------- main ----------
async function main() {
    const terms = JSON.parse(await fs.readFile(TERMS_FILE, 'utf8'));
    if (!Array.isArray(terms) || terms.length === 0) {
        throw new Error('terms.json must be a non-empty array of strings');
    }

    await ensureDir(OUTPUT_DIR);

    const { browser, page } = await createStealthBrowser({
        // optional overrides if the helper accepts them
        // headless: false is forced by the skill
    });

    try {
        for (let i = 0; i < terms.length; i++) {
            const term = terms[i].trim();
            if (!term) continue;

            console.log(`\n[${i + 1}/${terms.length}] Searching: "${term}"`);

            // Go to search engine
            await page.goto(SEARCH_ENGINE + encodeURIComponent(term), {
                waitUntil: 'domcontentloaded',
                timeout: 45000,
            });

            // Human-like mouse movement
            await simulateMouseMovement(page);

            // Extra short random pause (looks more natural)
            await page.waitForTimeout(randomDelay(800, 2200));

            // Wait for results to settle
            await page.waitForSelector('body', { timeout: 15000 });

            // Optional: scroll a bit like a human
            await page.evaluate(() => {
                window.scrollBy(0, Math.floor(Math.random() * 600) + 200);
            });
            await page.waitForTimeout(randomDelay(600, 1500));

            // Scrape + screenshot
            await saveResults(term, page, i + 1);

            // Longer random delay before next search
            if (i < terms.length - 1) {
                const delay = randomDelay();
                console.log(`  waiting ${Math.round(delay / 1000)}s…`);
                await page.waitForTimeout(delay);
            }
        }

        console.log('\nDone. All results in ./output/');
    } finally {
        // CRITICAL — always close, otherwise the process hangs
        await browser.close();
    }
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});