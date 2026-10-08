import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createStealthBrowser, simulateMouseMovement } from './scripts/stealth-template.mjs';

const INPUT_JSON = process.env.INPUT_JSON || '';
const OUTPUT_DIR = process.env.EVIDENCE_OUTPUT_DIR || process.env.AMP_OUTPUT_DIR || './evidence-output';
const MAX_LINKS = Number.parseInt(process.env.MAX_LINKS || '10', 10);
const REAL_CHROME_PATH = process.env.CHROME_PATH || 'C:\\Users\\AI Fiham\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';
const LANDING_NAVIGATION_TIMEOUT_MS = 60000;
const LANDING_SETTLE_MS = 10000;
const AMP_MAX_WAIT_MS = Number.parseInt(process.env.AMP_MAX_WAIT_MS || '120000', 10);
const AMP_POLL_MS = 2000;
const PARALLEL_TABS = Number.parseInt(process.env.PARALLEL_TABS || '5', 10);

function safeName(value) {
    return String(value || 'link')
        .replace(/[^a-z0-9]+/gi, '_')
        .replace(/^_|_$/g, '')
        .slice(0, 80) || 'link';
}

async function findInputJson() {
    if (INPUT_JSON) return path.resolve(INPUT_JSON);

    const rootCandidate = path.resolve('./search_results.json');
    const outputRoot = path.resolve('./output');
    if (fsSync.existsSync(outputRoot)) {
        const cycles = (await fs.readdir(outputRoot, { withFileTypes: true }))
            .filter(entry => entry.isDirectory() && entry.name.startsWith('cycle_'))
            .map(entry => entry.name)
            .sort()
            .reverse();

        for (const cycle of cycles) {
            const candidate = path.join(outputRoot, cycle, 'search_results.json');
            if (fsSync.existsSync(candidate)) return candidate;
        }
    }

    if (fsSync.existsSync(rootCandidate)) return rootCandidate;

    throw new Error('Could not find search_results.json. Set INPUT_JSON to its path.');
}

async function loadLinks(jsonPath) {
    const data = JSON.parse(await fs.readFile(jsonPath, 'utf8'));
    const records = Array.isArray(data) ? data : [data];
    const links = [];

    for (const record of records) {
        const brand = String(record.brand || 'unknown');
        for (const result of Array.isArray(record.results) ? record.results.slice(0, MAX_LINKS) : []) {
            if (!/^https?:\/\//i.test(result.url || '')) continue;
            links.push({
                brand,
                rank: result.rank || links.length + 1,
                title: result.title || '',
                url: result.url
            });
        }
    }

    if (links.length === 0) throw new Error(`No HTTP links found in ${jsonPath}`);
    return links;
}

async function waitForAmpCompletion(page) {
    const deadline = Date.now() + AMP_MAX_WAIT_MS;

    while (Date.now() < deadline) {
        const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
        const valid = /valid amp page|amp page is valid|valid amp/.test(bodyText);
        const invalid = /not an amp page|not a valid amp|invalid amp|does not use amp/.test(bodyText);
        const viewTestedPage = getViewTestedPage(page);
        const hasViewTestedPage = await viewTestedPage.isVisible().catch(() => false);

        if (valid || invalid || hasViewTestedPage) {
            return { status: !invalid && (valid || hasViewTestedPage) ? 'valid' : 'invalid' };
        }

        await page.waitForTimeout(AMP_POLL_MS);
    }

    return { status: 'timeout' };
}

async function clickScreenshotTab(page) {
    const screenshotTab = page.locator('[role="tab"]')
        .filter({ hasText: /^SCREENSHOT$/i })
        .first();
    const exactLabel = page.getByText('SCREENSHOT', { exact: true }).first();
    const tab = await screenshotTab.isVisible().catch(() => false) ? screenshotTab : exactLabel;

    await tab.waitFor({ state: 'visible', timeout: 10000 });
    await tab.click({ timeout: 10000 });
    await page.waitForTimeout(3000);

    const selected = await tab.getAttribute('aria-selected').catch(() => null);
    return selected !== 'false';
}

function getViewTestedPage(page) {
    return page.locator(
        '[aria-label*="view tested page" i], [title*="view tested page" i], ' +
        'a, button, [role="button"]'
    ).filter({ hasText: /view tested page/i }).first();
}

async function solveCloudflareTurnstile(page, timeoutMs = 30000, options = {}) {
    const { logPrefix = 'CF', requireToken = true } = options;
    const deadline = Date.now() + timeoutMs;

    const extractTurnstileToken = async () => {
        const token = await page.locator('input[name="cf-turnstile-response"]').inputValue().catch(() => '');
        return token && token.length > 30 ? token : '';
    };

    while (Date.now() < deadline) {
        const token = await extractTurnstileToken();
        if (token) {
            console.log(`  [${logPrefix}] Turnstile solved with token length ${token.length}.`);
            return { solved: true, token };
        }

        const widgetVisible = await page.locator('.cf-turnstile, [data-sitekey]').first().isVisible().catch(() => false);
        const challengeVisible = await page.locator('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], iframe[title*="Cloudflare"]').first().isVisible().catch(() => false);

        if (!widgetVisible && !challengeVisible) {
            await page.waitForTimeout(1000);
            continue;
        }

        try {
            const directWidget = page.locator('.cf-turnstile').first();
            if (await directWidget.isVisible().catch(() => false)) {
                await directWidget.click({ position: { x: 24, y: 20 } });
                console.log(`  [${logPrefix}] Clicked .cf-turnstile widget.`);
            }
        } catch (error) {
            console.warn(`  [${logPrefix}] Direct widget click skipped: ${error.message}`);
        }

        try {
            const frames = page.frames();
            for (const frame of frames) {
                if (!frame.url() || !/challenges\.cloudflare\.com|turnstile/i.test(frame.url())) continue;

                for (const selector of ['input[type="checkbox"]', '.cb-i', '.ctp-checkbox-label', 'label']) {
                    const candidate = frame.locator(selector).first();
                    if (await candidate.isVisible().catch(() => false)) {
                        await candidate.click({ timeout: 5000 });
                        console.log(`  [${logPrefix}] Clicked Turnstile checkbox inside iframe.`);
                        break;
                    }
                }
            }
        } catch (error) {
            console.warn(`  [${logPrefix}] Challenge iframe click skipped: ${error.message}`);
        }

        try {
            const token = await extractTurnstileToken();
            if (token) {
                console.log(`  [${logPrefix}] Turnstile token received.`);
                return { solved: true, token };
            }
        } catch (error) {
            console.warn(`  [${logPrefix}] Token extraction failed: ${error.message}`);
        }

        await simulateMouseMovement(page, 3);
        await page.waitForTimeout(1500);
    }

    if (requireToken) {
        console.warn(`  [${logPrefix}] Cloudflare Turnstile still not solved before timeout.`);
    }

    return { solved: false, token: null };
}

async function createEvidencePaths(outputDir, link, index) {
    const domain = new URL(link.url).hostname.replace(/^www\./i, '')
        .replace(/[^a-z0-9.-]/gi, '_');
    // Keep separate evidence for multiple URLs on the same domain.
    const urlDir = path.join(outputDir, safeName(link.brand), domain, String(index).padStart(3, '0'));
    await fs.mkdir(urlDir, { recursive: true });
    return {
        landingPath: path.join(urlDir, 'landing.png'),
        textPath: path.join(urlDir, 'landing.txt'),
        ampPath: path.join(urlDir, 'amp-test.png'),
        testedPagePath: path.join(urlDir, 'tested-page.png')
    };
}

async function captureLink(page, link, index, total, outputDir) {
    const { landingPath, textPath, ampPath, testedPagePath } =
        await createEvidencePaths(outputDir, link, index);
    const result = {
        ...link,
        landing_screenshot: landingPath,
        landing_text_status: 'skipped',
        amp_test_screenshot: ampPath,
        tested_page_screenshot: testedPagePath
    };

    console.log(`[${index}/${total}] Landing page: ${link.url}`);
    try {
        await page.goto(link.url, { waitUntil: 'domcontentloaded', timeout: LANDING_NAVIGATION_TIMEOUT_MS });
        const turnstileResult = await solveCloudflareTurnstile(page, 30000, { logPrefix: 'LANDING' });
        if (turnstileResult.solved) {
            result.turnstile_solved = true;
            result.turnstile_token_length = turnstileResult.token?.length || 0;
        }
        await page.waitForLoadState('networkidle', { timeout: LANDING_NAVIGATION_TIMEOUT_MS }).catch(() => {
            console.log('  Network idle was not reached; continuing after render wait.');
        });
        console.log(`  Waiting ${Math.round(LANDING_SETTLE_MS / 1000)}s for the landing page to finish rendering...`);
        await page.waitForTimeout(LANDING_SETTLE_MS);
        await simulateMouseMovement(page, 3);
        try {
            const content = await page.content();
            await fs.writeFile(textPath, content, 'utf8');
            result.landing_text_file = textPath;
            result.landing_text_status = 'ok';
            console.log(`  Full page HTML saved to ${textPath}`);
        } catch (error) {
            result.landing_text_status = 'error';
            result.landing_text_error = error.message;
            console.warn(`  Page HTML could not be saved: ${error.message}`);
        }
        await page.screenshot({ path: landingPath, fullPage: true });
        result.landing_url = page.url();
        result.landing_status = 'ok';
    } catch (error) {
        result.landing_status = 'error';
        result.landing_error = error.message;
        console.warn(`  Landing page failed: ${error.message}`);
    }

    const ampTestUrl = `https://search.google.com/test/amp?url=${encodeURIComponent(link.url)}`;
    console.log(`[${index}/${total}] AMP test`);
    try {
        await page.goto(ampTestUrl, { waitUntil: 'domcontentloaded', timeout: AMP_MAX_WAIT_MS });
        const turnstileResult = await solveCloudflareTurnstile(page, 30000, { logPrefix: 'AMP' });
        if (turnstileResult.solved) {
            result.amp_turnstile_solved = true;
            result.amp_turnstile_token_length = turnstileResult.token?.length || 0;
        }
        console.log(`  Waiting for AMP completion (maximum ${Math.round(AMP_MAX_WAIT_MS / 1000)}s)...`);
        const ampResult = await waitForAmpCompletion(page);
        await page.screenshot({ path: ampPath, fullPage: true });
        result.amp_test_url = page.url();
        result.amp_test_status = ampResult.status;

        if (ampResult.status === 'valid') {
            const viewTestedPage = getViewTestedPage(page);

            if (await viewTestedPage.isVisible().catch(() => false)) {
                await viewTestedPage.click({ timeout: 10000 });
                await page.waitForTimeout(3000);
                result.view_tested_page_clicked = true;
                result.screenshot_tab_clicked = await clickScreenshotTab(page);
                await page.screenshot({ path: testedPagePath, fullPage: true });
            } else {
                result.view_tested_page_clicked = false;
                result.screenshot_tab_clicked = false;
            }
        }
    } catch (error) {
        result.amp_test_status = 'error';
        result.amp_test_error = error.message;
        console.warn(`  AMP test failed: ${error.message}`);
    }

    return result;
}

async function main() {
    const inputJson = await findInputJson();
    const links = await loadLinks(inputJson);
    const runDir = path.join(path.resolve(OUTPUT_DIR), new Date().toISOString().replace(/[:.]/g, '-'));
    await fs.mkdir(runDir, { recursive: true });

    const executablePath = fsSync.existsSync(REAL_CHROME_PATH) ? REAL_CHROME_PATH : null;
    const { browser, context, page } = await createStealthBrowser({
        headless: false,
        viewport: { width: 1366, height: 768 },
        executablePath
    });

    const pagePool = [page];
    const tabLimit = Math.max(1, Math.min(PARALLEL_TABS, links.length || 1));
    const results = [];

    try {
        for (let start = 0; start < links.length; start += tabLimit) {
            const batch = links.slice(start, start + tabLimit);
            const batchPages = [];

            for (let i = 0; i < batch.length; i++) {
                if (pagePool.length <= i) {
                    pagePool.push(await context.newPage());
                }
                batchPages.push(pagePool[i]);
            }

            const batchResults = await Promise.all(
                batch.map((link, index) => captureLink(
                    batchPages[index],
                    link,
                    start + index + 1,
                    links.length,
                    runDir
                ))
            );

            results.push(...batchResults);
        }
    } finally {
        await browser.close();
    }

    await fs.writeFile(path.join(runDir, 'evidence_results.json'), JSON.stringify({ input_json: inputJson, results }, null, 2));
    console.log(`Finished. Screenshots, HTML content in .txt files, and evidence_results.json saved to ${runDir}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    main().catch(error => {
        console.error(`[!] Fatal execution error: ${error.message}`);
        process.exitCode = 1;
    });
}
