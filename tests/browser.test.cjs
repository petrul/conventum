// Mandatory browser suite, invoked by rake test against a copied database.
// A plain Python static server proves that no application API is required.
const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

(async () => {
  const server = spawn(process.env.DIC_PYTHON || 'python3',['-u','-m','http.server','0','--bind','127.0.0.1','--directory',path.resolve(process.env.DIC_STATIC_DIR || 'dist')],{detached:true,stdio:['ignore','pipe','pipe']});
  let browser;
  try {
    const port = await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(new Error('Static server startup timed out')),15000);
      server.stdout.on('data',chunk=>{const match=chunk.toString().match(/port (\d+)/);if(match){clearTimeout(timeout);resolve(Number(match[1]));}});
      server.on('error',reject);server.on('exit',code=>reject(new Error('Static server exited '+code)));
    });
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [], remoteRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {if (/^https?:/.test(request.url())) remoteRequests.push(request.url());});
    await page.goto(`http://127.0.0.1:${port}/`);
    await page.waitForFunction(()=>window.dicReady===true,{},{timeout:60000});
    await page.waitForSelector('.word-row');
    assert.equal(await page.title(), 'Dic · An atlas of Romanian words');
    assert.equal(await page.locator('.brand').textContent(), 'dic.');
    const search = async value => {
      await page.locator('#search').fill(value);
      await page.waitForFunction(value => new URLSearchParams(location.hash.slice(1)).get('q') === value, value);
    };
    const sorted = async descending => {
      const distances = (await page.locator('.distance').allTextContents()).filter(text => text !== '—').map(Number);
      assert(distances.length, 'Expected scored words');
      for (let i=1; i<distances.length; i++) assert(descending ? distances[i-1]>=distances[i] : distances[i-1]<=distances[i], 'Scores must be sorted');
    };
    await page.getByRole('tab', {name:/^Latin/}).click();
    for (const [query, expected] of [['conventum','cuvânt'],['frînghie','frânghie'],['pămînt','pământ'],['mormînt','mormânt'],['bătrîn','bătrân'],['a cîștiga','câștiga'],['rost','rost']]) {
      await search(query);
      assert((await page.locator('.word').allTextContents()).includes(expected), `${query} must find ${expected}`);
      const suggestions = await page.locator('#word-suggestions option').evaluateAll(options=>options.map(o=>o.value));
      assert(suggestions.includes(expected),`${query} must autocomplete from SQLite`);
      const matchingRow=page.locator('.word-row').filter({has:page.locator('.word').filter({hasText:expected})}).first();
      assert((await matchingRow.locator('.etymology-cell').textContent()).includes('latină'),'Source etymology must be visible');
      if(expected==='pământ') assert((await matchingRow.locator('.etymology-cell').textContent()).includes('pavimentum'));
      if(expected==='cuvânt') assert(Number(await matchingRow.locator('.distance').textContent())>0.45);
    }
    await page.locator('h1').click();
    await page.keyboard.press('/');
    assert(await page.locator('#search').evaluate(input=>input===document.activeElement),'Slash must focus search');
    await page.locator('#search').press('End');
    await page.keyboard.press('/');
    assert((await page.locator('#search').inputValue()).endsWith('/'),'Slash typed in search must not be intercepted');
    await search('');
    await page.selectOption('#sort', 'desc'); await sorted(true);
    assert((await page.locator('.selected-origin .tag').allTextContents()).every(group=>group==='Latin'));
    await page.selectOption('#sort', 'asc'); await sorted(false);
    await search('casele');
    assert((await page.locator('.word').allTextContents()).includes('casă'), 'Inflections should find their lemma');
    await search('pamant');
    assert((await page.locator('.ancestors').allTextContents()).includes('paumentum'));
    assert(!(await page.locator('.ancestors').allTextContents()).includes('pavimentum'));
    await page.check('#ancestors');
    assert((await page.locator('.ancestors').allTextContents()).includes('pavimentum'), 'Deeper ancestry must be opt-in');
    await search('mormant');
    const ancestors = await page.locator('.ancestors').allTextContents();
    assert(ancestors.includes('monumentum'));
    for (const invalid of ['r','mor','morimentu','morimen']) assert(!ancestors.includes(invalid), 'Comparison terms must not be Latin ancestors');
    await search(''); await page.uncheck('#ancestors');
    await page.getByRole('tab', {name:/^Turkic/}).click();
    await page.selectOption('#sort', 'desc'); await sorted(true);
    assert((await page.locator('.selected-origin .tag').allTextContents()).every(group=>group==='Turkic'));
    await page.getByRole('tab', {name:/^All/}).click();
    await page.selectOption('#metric', 'definitions');
    await page.check('#scored-only'); await sorted(true);
    await page.locator('.score-details summary').first().click();
    assert((await page.locator('.score-details[open] .forms').allTextContents()).length === 2);
    const fragment = await page.evaluate(()=>location.hash);
    await page.reload(); await page.waitForSelector('.word-row');
    assert.equal(await page.evaluate(()=>location.hash), fragment, 'Shared URLs must preserve ranking settings');
    await page.uncheck('#scored-only');
    await page.selectOption('#metric','words');
    await page.getByRole('tab', {name:/^Latin/}).click();
    await search('casa');
    await page.waitForTimeout(200);
    await page.screenshot({path:'/tmp/dic-distance-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile page must not overflow');
    await page.screenshot({path:'/tmp/dic-distance-mobile.png',fullPage:true});
    assert.deepEqual(errors,[],'Browser errors');
    assert(remoteRequests.some(url=>url.endsWith('/dic.sqlite')),'The browser must fetch its SQLite file');
    assert(remoteRequests.some(url=>url.endsWith('/sql-wasm.wasm')),'The browser must load SQLite WebAssembly');
    assert(remoteRequests.every(url=>new URL(url).origin===`http://127.0.0.1:${port}`),'Static site must not contact Ollama, APIs, or CDNs');
    console.log('Browser regressions passed: distance directions, scoped scores, grouped forms, ancestry depth, parser examples, definition inputs, shareable state, autocomplete, visible etymologies, slash shortcut, mobile layout, static SQLite operation.');
  } finally {if(browser)await browser.close();try{process.kill(-server.pid,'SIGTERM');}catch{}}
})().catch(error=>{console.error(error);process.exitCode=1;});
