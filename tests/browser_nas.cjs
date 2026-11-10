// Read-only NAS browsing through the running local service; no import or export.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const nas = process.env.QA_NAS_PATH;
if (!nas) { console.log('SKIP: set QA_NAS_PATH to test your own NAS folder.'); process.exit(0); }
const subfolder = process.env.QA_NAS_SUBFOLDER;

(async () => {
  const browser = await chromium.launch({...(process.env.QA_BROWSER ? {executablePath: process.env.QA_BROWSER} : {}), headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1080}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765');
    await page.waitForSelector('.import-main');
    await page.locator('.import-main').click();
    await page.locator('[data-browse="import-path"]').click();
    await page.waitForSelector('.folder-location');
    await page.locator('#folder-address').fill(nas);
    await page.locator('#folder-address-form button[type="submit"]').click();
    await page.waitForSelector('.folder-entry', {timeout: 35000});
    await page.waitForFunction(() => !document.querySelector('#folder-select').disabled);
    await page.screenshot({path: path.join(__dirname, '..', 'qa', 'nas-browser-desktop.png')});
    if (subfolder) {
      await page.locator('.folder-entry', {hasText: subfolder}).click();
      await page.waitForFunction(name => document.querySelector('#folder-address').value.endsWith(name) && !document.querySelector('#folder-select').disabled, subfolder);
      await page.locator('#folder-up').click();
      await page.waitForFunction(expected => document.querySelector('#folder-address').value.replace(/\\+$/, '') === expected.replace(/\\+$/, '') && !document.querySelector('#folder-select').disabled, nas);
    }
    await page.locator('#folder-select').click();
    await page.waitForSelector('#folder-dialog:not([open])', {state: 'attached'});
    assert.equal(await page.locator('#import-path').inputValue(), nas);
    await page.locator('[data-close="import-dialog"]').first().click();
    assert.equal(errors.length, 0, errors.join('; '));
    console.log('PASS: running service NAS listing, subfolder navigation, parent, filter and folder selection. No scan/export submitted.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
