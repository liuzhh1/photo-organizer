/* Run with NODE_PATH pointing at a Playwright installation. */
const { chromium } = require('playwright');
const { spawn, spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const python = process.env.QA_PYTHON || 'python';
const browserPath = process.env.QA_BROWSER;
const qa = path.join(root, 'qa');
fs.mkdirSync(qa, {recursive: true});
spawnSync(python, [path.join(__dirname, 'create_samples.py')], {cwd: root, stdio: 'inherit'});
const port = 18767;
const server = spawn(python, ['-u', 'app.py', '--no-browser', '--port', String(port), '--data', path.join(qa, `ui-data-${Date.now()}`)], {cwd: root, stdio: 'pipe'});
let browser;

(async () => {
  const url = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let n = 0; n < 100; n++) {
    try { await fetch(url + '/api/state'); ready = true; break; } catch (_) { await new Promise(resolve => setTimeout(resolve, 200)); }
  }
  assert(ready, 'test server started');
  browser = await chromium.launch({...(browserPath ? {executablePath: browserPath} : {}), headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1080}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(url);
  await page.waitForSelector('#empty:not(.hidden)');
  await page.screenshot({path: path.join(qa, 'empty-desktop.png'), fullPage: true});

  await page.locator('.settings-link').click();
  await page.waitForSelector('#settings-dialog[open]');
  assert.equal(await page.locator('.category-row').count(), 8);
  await page.locator('[data-close="settings-dialog"]').first().click();

  await page.locator('.import-main').click();
  await page.locator('[data-browse="import-path"]').click();
  await page.waitForSelector('#folder-dialog[open]');
  await page.waitForSelector('.folder-location');
  await page.locator('#folder-address').fill(path.join(qa, 'samples'));
  await page.locator('#folder-address-form button[type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('#folder-select').disabled);
  await page.screenshot({path: path.join(qa, 'folder-browser-desktop.png')});
  await page.locator('#folder-select').click();
  await page.waitForSelector('#folder-dialog:not([open])', {state:'attached'});
  assert.equal((await page.locator('#import-path').inputValue()).toLowerCase(), path.join(qa, 'samples').toLowerCase());
  await page.locator('#import-form button[type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#stats [data-count="total"]').textContent === '8', {timeout: 30000});
  await page.waitForSelector('.photo-card');
  assert.equal(await page.locator('.photo-card').count(), 8);
  await page.screenshot({path: path.join(qa, 'library-desktop.png'), fullPage: true});

  await page.locator('.nav-item[data-view="duplicates"]').click();
  await page.waitForSelector('.group-header');
  assert((await page.locator('.photo-card').count()) >= 2);
  await page.locator('#recommend').click();
  await page.waitForSelector('#selection-bar:not(.hidden)');
  await page.locator('#clear-selection').click();

  await page.locator('.nav-item[data-view="quality"]').click();
  await page.waitForSelector('#context-banner:not(.hidden)');
  assert((await page.locator('.photo-card').count()) > 0);

  await page.locator('.nav-item[data-view="all"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.photo-card').length === 8);
  await page.locator('.photo-image').first().click();
  await page.waitForSelector('#detail-dialog[open]');
  await page.locator('#detail-keep').click();
  await page.waitForFunction(() => document.querySelector('#detail-metrics').textContent.includes('已保留'));
  await page.keyboard.press('ArrowRight');
  await page.locator('#detail-reject').click();
  await page.waitForFunction(() => document.querySelector('#detail-metrics').textContent.includes('已排除'));
  await page.screenshot({path: path.join(qa, 'preview-desktop.png')});
  await page.keyboard.press('Escape');

  await page.locator('.nav-item[data-view="kept"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.photo-card').length === 1);
  await page.locator('#select-all').check();
  await page.locator('#selection-bar [data-action="export"]').click();
  await page.locator('[data-browse="export-path"]').click();
  await page.waitForSelector('#folder-dialog[open]');
  await page.locator('#folder-address').fill(path.join(qa, 'exports'));
  await page.locator('#folder-address-form button[type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('#folder-select').disabled);
  await page.locator('#folder-select').click();
  await page.waitForSelector('#folder-dialog:not([open])', {state:'attached'});
  await page.locator('#export-form button[type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#job-result').textContent.includes('已复制 1 张'), {timeout: 30000});
  await page.locator('#clear-selection').click();

  await page.locator('.nav-item[data-view="all"]').click();
  await page.locator('#search-input').fill('DSC_1001');
  await page.locator('#search-form button[type="submit"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.photo-card').length === 2);
  await page.locator('#clear-search').click();
  await page.waitForFunction(() => document.querySelectorAll('.photo-card').length === 8);

  await page.locator('.settings-link').click();
  await page.locator('#quality-threshold').fill('60');
  await page.locator('#settings-form button[type="submit"]').click();
  await page.waitForSelector('#settings-dialog:not([open])', {state:'attached'});

  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(qa, 'library-mobile.png'), fullPage: true});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile has no horizontal overflow');
  await page.locator('.import-main').click();
  await page.waitForSelector('#import-dialog[open]');
  await page.locator('[data-browse="import-path"]').click();
  await page.waitForSelector('#folder-dialog[open]');
  await page.waitForSelector('.folder-location');
  await page.screenshot({path: path.join(qa, 'folder-browser-mobile.png')});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile folder browser has no overflow');
  await page.locator('#folder-address').fill(path.join(qa, 'missing-folder'));
  await page.locator('#folder-address-form button[type="submit"]').click();
  await page.waitForSelector('#folder-error:not(.hidden)');
  assert(await page.locator('#folder-select').isDisabled(), 'inaccessible folder cannot be selected');
  await page.keyboard.press('Escape');
  await page.screenshot({path: path.join(qa, 'import-mobile.png')});
  assert.equal(errors.length, 0, `browser errors: ${errors.join('; ')}`);
  console.log('PASS: empty state, settings, import, duplicates, quality, preview, reviews, export, filename search, mobile layout; no browser JS errors.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  server.kill();
});
