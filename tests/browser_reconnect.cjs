// A real local server restart: preserve the old page and renew its write token.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const data = path.join(root, 'qa', `reconnect-${Date.now()}`);
fs.mkdirSync(data, {recursive: true});
const port = 18768;
let server, browser;
const start = () => {
  server = spawn(process.env.QA_PYTHON || 'python', ['app.py', '--no-browser', '--port', String(port), '--data', data], {cwd:root, stdio:'pipe'});
};
const waitReady = async () => {
  for (let n=0;n<100;n++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/health`); if(r.ok) return; } catch(_) {}
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error('server not ready');
};
const stop = async () => { await new Promise(resolve => {server.once('exit',resolve);server.kill();}); };
(async()=>{
  start(); await waitReady();
  browser=await chromium.launch({...(process.env.QA_BROWSER ? {executablePath:process.env.QA_BROWSER} : {}),headless:true});
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}`);
  await page.waitForSelector('#empty:not(.hidden)');
  const originalToken=await page.locator('meta[name="session-token"]').getAttribute('content');
  await stop();
  await page.waitForSelector('#connection-banner:not(.hidden)');
  start();await waitReady();
  await page.waitForSelector('#connection-banner.hidden', {state:'attached'});
  assert.equal(await page.locator('meta[name="session-token"]').getAttribute('content'),originalToken,'page was not reloaded');
  await page.locator('.settings-link').click();
  await page.locator('#quality-threshold').fill('71');
  await page.locator('#settings-form button[type="submit"]').click();
  await page.waitForSelector('#settings-dialog:not([open])',{state:'attached'});
  const state=await (await fetch(`http://127.0.0.1:${port}/api/state`)).json();
  assert.equal(state.config.quality_threshold,71,'write succeeded after renewing old session token');
  const validation=await page.evaluate(async()=>{
    const home=new DOMParser().parseFromString(await (await fetch('/')).text(),'text/html');
    const token=home.querySelector('meta[name="session-token"]').content;
    const result=await fetch('/api/classify',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:'{}'});
    return {status:result.status,body:await result.json()};
  });
  assert.equal(validation.status,400);
  assert(validation.body.error.includes('模型'));
  assert.equal((await fetch(`http://127.0.0.1:${port}/api/health`)).status,200);
  assert.equal(errors.length,0,errors.join(';'));
  console.log('PASS: lost connection notice, reconnect without reloading, write-token renewal, missing-model error, service stays healthy.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();if(server&&!server.killed)server.kill();});
