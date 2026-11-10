// Validate the actual AI buttons against the configured local production model.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async()=>{
  const browser=await chromium.launch({...(process.env.QA_BROWSER ? {executablePath:process.env.QA_BROWSER} : {}),headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1080}});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:8765');
    await page.waitForSelector('.photo-card');
    await page.waitForFunction(()=>!document.querySelector('#classify').disabled);
    const priorJob = await page.evaluate(async()=> (await (await fetch('/api/state')).json()).job.id);
    await page.locator('#classify').click();
    await page.waitForFunction(async oldJob=>{
      const state=await (await fetch('/api/state')).json();
      return state.job.id!==oldJob&&state.job.type==='classify'&&!state.job.running;
    },priorJob,{timeout:60000});
    const state=await page.evaluate(async()=>await (await fetch('/api/state')).json());
    assert.equal(state.job.errors.length,0,JSON.stringify(state.job.errors));
    assert.equal(state.job.total,state.stats.total-state.stats.rejected);
    assert(state.stats.classified>=state.stats.total-state.stats.rejected);
    assert.equal(state.model.active_device,'cuda');
    await page.locator('#search-mode').selectOption('semantic');
    await page.locator('#search-input').fill('新西兰的雪山和湖泊');
    await page.locator('#search-form button[type="submit"]').click();
    await page.waitForSelector('#search-label:not(.hidden)',{timeout:30000});
    assert((await page.locator('#search-label').textContent()).includes('语义检索'));
    assert((await page.locator('.photo-card').count())>0);
    assert.equal(errors.length,0,errors.join(';'));
    console.log(`PASS: actual AI classification button (${state.stats.classified} classified), CUDA and Chinese semantic search in production browser; zero JS errors.`);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
