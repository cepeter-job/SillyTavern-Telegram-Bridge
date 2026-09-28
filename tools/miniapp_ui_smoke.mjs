// Usage: MINIAPP_JSDOM_ROOT=/path/with/node_modules PYTHON=/path/to/python node --experimental-vm-modules tools/miniapp_ui_smoke.mjs
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import vm from 'node:vm';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {JSDOM}=await import(pathToFileURL(resolve(process.env.MINIAPP_JSDOM_ROOT||root,'node_modules/jsdom/lib/api.js')));
const child=spawn(process.env.PYTHON||'python3',[resolve(root,'tests/miniapp_browser_fixture.py')],{cwd:root,stdio:['ignore','pipe','inherit']});
const lines=createInterface({input:child.stdout});
const ready=await Promise.race([new Promise((ok,bad)=>{lines.once('line',line=>{try{ok(JSON.parse(line));}catch(e){bad(e);}});child.once('exit',code=>bad(new Error('Fixture exited '+code)));}),new Promise((_,bad)=>setTimeout(()=>bad(new Error('Fixture startup timed out')),10000).unref())]);
const html=await readFile(resolve(root,'bridge/miniapp_assets/index.html'),'utf8');
const css=await readFile(resolve(root,'bridge/miniapp_assets/style.css'),'utf8');
const dom=new JSDOM(html,{url:ready.url+'/miniapp/',runScripts:'outside-only',pretendToBeVisual:true});
const style=dom.window.document.createElement('style');style.textContent=css;dom.window.document.head.append(style);
const initialDocument=dom.window.document;
assert.ok(initialDocument.querySelector('.app-shell'),'Telegram-native app shell is present');
assert.ok(initialDocument.querySelector('.app-header .connection-dot'),'Compact header exposes connection status');
assert.ok(initialDocument.querySelector('#content .skeleton-shell'),'Initial content uses a skeleton instead of a loading card');
assert.ok(initialDocument.querySelector('#more-menu.more-sheet'),'Secondary navigation has a More sheet');
const context=dom.getInternalVMContext(),errors=[],expectedFailures=new Set(),observedFailures=[];
let portraitMode='success',scrollResets=0;
dom.window.scrollTo=()=>{scrollResets++;};
process.on('unhandledRejection',error=>errors.push(error));
dom.window.Telegram={WebApp:{initData:ready.initData,initDataUnsafe:{start_param:'dashboard'},ready(){},expand(){},BackButton:{onClick(){},hide(){},show(){}}}};
dom.window.AbortSignal=globalThis.AbortSignal;
dom.window.URL.createObjectURL=()=> 'blob:test-fixture';
dom.window.URL.revokeObjectURL=()=>{};
dom.window.fetch=async (path,options)=>{
  assert.ok(String(path).startsWith('/api/v1/'),'Only local API requests are permitted');
  if(/^\/api\/v1\/characters\/[^/]+\/portrait$/.test(String(path))) {
    return new Response(new Blob([portraitMode==='success'?'portrait':''],{type:'image/png'}),{status:200,headers:{'Content-Type':'image/png'}});
  }
  const response=await fetch(ready.url+path,options);
  if(!response.ok&&expectedFailures.has(response.status+' '+path))observedFailures.push(response.status+' '+path);
  if(!response.ok&&!expectedFailures.has(response.status+' '+path))errors.push(new Error('API '+response.status+' '+path));
  return response;
};
dom.window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
dom.window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
const modules=new Map();
async function moduleFor(name) {
  name=name.replace(/^\.\//,'');assert.match(name,/^[a-z]+\.js$/);
  if(modules.has(name))return modules.get(name);
  const source=await readFile(resolve(root,'bridge/miniapp_assets',name),'utf8');
  const module=new vm.SourceTextModule(source,{context,identifier:name,importModuleDynamically:async spec=>{
    const loaded=await moduleFor(spec);if(loaded.status==='unlinked')await loaded.link(linker);
    if(loaded.status==='linked')await loaded.evaluate();return loaded;
  }});
  modules.set(name,module);return module;
}
async function linker(spec){return moduleFor(spec);}
async function until(predicate,label) {
  for(let i=0;i<150;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20));}
  throw new Error('Timed out: '+label+' | '+dom.window.document.body.textContent.slice(-1000));
}
try {
  const app=await moduleFor('app.js');await app.link(linker);await app.evaluate();
  await until(()=>dom.window.document.querySelectorAll('#navigation button').length===5,'five primary navigation actions');
  const shellDocument=dom.window.document;
  assert.deepEqual([...shellDocument.querySelectorAll('#navigation button')].map(n=>n.textContent.trim()),['Home','Characters','Usage','Sessions','More']);
  const more=[...shellDocument.querySelectorAll('#navigation button')].find(n=>n.textContent.trim()==='More');
  assert.equal(shellDocument.querySelectorAll('#navigation svg').length,5,'Every primary destination has a local SVG icon');
  assert.ok(shellDocument.querySelector('.skip-link'),'Keyboard users can skip navigation');
  more.click();
  await until(()=>shellDocument.querySelector('#more-menu[open]'),'More sheet opens');
  assert.deepEqual([...shellDocument.querySelectorAll('#more-navigation button')].map(n=>n.getAttribute('aria-label')||n.textContent.trim()),['Models','Memory','Personas','Worlds','Data Bank','System']);
  shellDocument.getElementById('more-menu').close();
  for(const page of ['dashboard','characters','usage','models','sessions','personas','worlds','memory','databank','system']) {
    await app.namespace.navigate(page);
    assert.ok(shellDocument.getElementById('page-title').textContent.trim().length>0,page+' updates the compact page title');
    const body=dom.window.document.querySelector('main').textContent;
    assert.ok(!body.includes('Could not load this page'),page+': '+body);
    assert.ok(body.length>30,page+' rendered content');
    if(page==='dashboard') {
      assert.ok(shellDocument.querySelector('.dashboard-hero'),'Dashboard uses a clear hero hierarchy');
      assert.ok(shellDocument.querySelector('.dashboard-session'),'Dashboard shows the current session summary');
      const sessionPortrait=shellDocument.querySelector('.dashboard-session .session-portrait');
      assert.ok(sessionPortrait,'Dashboard shows the active character portrait');
      await until(()=>sessionPortrait.getAttribute('src')==='blob:test-fixture','dashboard portrait loaded');
      sessionPortrait.dispatchEvent(new dom.window.Event('load'));
      assert.equal(sessionPortrait.closest('.session-portrait-frame').hidden,false,'Dashboard portrait appears only after loading');
      assert.equal(dom.window.getComputedStyle(sessionPortrait).objectFit,'contain','Dashboard portrait preserves the full image');
      assert.equal(shellDocument.querySelectorAll('.dashboard-shortcuts button').length,4,'Dashboard exposes four quick actions');
      portraitMode='empty';
    }
    if(page==='usage') {
      assert.ok(shellDocument.querySelector('.usage-dashboard'),'Dedicated usage page renders');
      assert.ok(shellDocument.querySelector('select[aria-label="Usage period"]'),'Period is selectable');
      assert.ok(shellDocument.querySelector('select[aria-label="Usage scope"]'),'Private session scope is selectable');
      assert.ok(body.includes('No recorded usage'),'Empty history is not presented as zero billed tokens');
      assert.ok(body.includes('provider-reported'),'Usage describes its measurement source');
    }
    if(page==='characters') {
      assert.ok(shellDocument.querySelector('.character-card .portrait-frame'),'Character cards use a portrait-first visual layout');
      const characterPortrait=shellDocument.querySelector('.character-card img.portrait');
      assert.equal(dom.window.getComputedStyle(characterPortrait).objectFit,'contain','Character portraits show the complete image without cropping');
      const pageDocument=dom.window.document;
      const rank=pageDocument.querySelector('video.rank-video source');
      assert.ok(rank,'Ranked character renders an animated rank asset');
      assert.equal(rank.getAttribute('src'),'/miniapp/ranks/rank_S.webm');
      await until(()=>[...pageDocument.querySelectorAll('img.portrait-broken')].some(n=>n.alt==='Portrait unavailable'),'empty portrait fallback');
    }
    console.log('render='+page+' ok');
  }
  await app.namespace.navigate('dashboard');
  await until(()=>shellDocument.querySelector('.dashboard-session .session-portrait-frame')?.hidden===true,'dashboard missing portrait fallback');
  assert.ok(shellDocument.querySelector('.dashboard-session').textContent.includes('Default session'),'Dashboard text remains when its portrait is unavailable');
  await app.namespace.navigate('models');
  const document=dom.window.document;
  const reasoningLabel=[...document.querySelectorAll('label')].find(n=>n.textContent==='Reasoning level');
  assert.ok(reasoningLabel,'Generation settings expose a named reasoning selector');
  const reasoning=document.getElementById(reasoningLabel.htmlFor);
  assert.equal(reasoning.tagName,'SELECT');
  assert.deepEqual([...reasoning.options].map(option=>[option.value,option.textContent]),[
    ['none','None (0)'],['low','Low (1,024)'],['medium','Medium (4,096)'],
    ['high','High (8,192)'],['max','Max (16,384)'],['custom','Custom'],
  ]);
  assert.equal(reasoning.value,'none');
  const customLabel=[...document.querySelectorAll('label')].find(n=>n.textContent.startsWith('Custom reasoning budget'));
  assert.ok(customLabel);
  const customBudget=document.getElementById(customLabel.htmlFor);
  assert.equal(customLabel.parentElement.hidden,true,'Custom budget is hidden for named levels');
  reasoning.value='low';reasoning.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  assert.equal(customLabel.parentElement.hidden,true);
  const label=[...document.querySelectorAll('label')].find(n=>n.textContent.startsWith('Temperature'));
  document.getElementById(label.htmlFor).value='0.65';
  const saveSettings=[...document.querySelectorAll('main button')].find(n=>n.textContent==='Save settings');
  saveSettings.click();
  await until(()=>document.getElementById('notice').textContent==='Generation settings saved.','save settings');
  let stored=await app.namespace.api('/generation');
  assert.equal(stored.settings.temperature,.65);assert.equal(stored.settings.reasoning_budget,1024);
  reasoning.value='custom';reasoning.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  assert.equal(customLabel.parentElement.hidden,false,'Custom budget is shown for Custom');
  customBudget.value='12345';document.getElementById('notice').textContent='';
  saveSettings.click();
  await until(()=>document.getElementById('notice').textContent==='Generation settings saved.','save custom reasoning budget');
  stored=await app.namespace.api('/generation');assert.equal(stored.settings.reasoning_budget,12345);
  // A page still displaying session A must not retarget an action when another view changes global state.
  const replacement=await app.namespace.api('/sessions',{method:'POST',body:app.namespace.sessionBody({title:'Other view session',operation_id:'dom-other-session'})});
  app.namespace.state.session=replacement.session;
  const before=(await app.namespace.api('/generation')).settings.temperature;
  document.getElementById(label.htmlFor).value='0.2';
  expectedFailures.add('409 /api/v1/generation');
  const oldSave=[...document.querySelectorAll('main button')].find(n=>n.textContent==='Save settings');
  oldSave.click();await until(()=>!oldSave.disabled,'stale-session save finished');
  const after=(await app.namespace.api('/generation')).settings.temperature;
  assert.equal(after,before,'A rendered session action must never silently mutate a different session');
  assert.ok(observedFailures.includes('409 /api/v1/generation'),'The server rejected the original rendered session as stale');
  expectedFailures.clear();
  await app.namespace.navigate('sessions');
  const input=[...document.querySelectorAll('input')].find(n=>n.placeholder==='New session title');
  input.value='DOM verified session';
  [...document.querySelectorAll('main button')].find(n=>n.textContent==='Create and open').click();
  await until(()=>document.querySelector('main').textContent.includes('DOM verified session'),'create session');
  const sessions=await app.namespace.api('/sessions');assert.equal(sessions.session.title,'DOM verified session');
  const info=await app.namespace.api('/characters/Alice.png');
  let job=await app.namespace.api('/characters/Alice.png/optimize',{method:'POST',body:app.namespace.sessionBody({digest:info.digest,suggestion:'Clarify motivation.',operation_id:'dom-optimizer'})});
  for(let i=0;i<50&&['queued','running'].includes(job.state);i++) {
    await new Promise(r=>setTimeout(r,50));job=await app.namespace.api('/jobs/'+job.id);
  }
  assert.equal(job.state,'succeeded',JSON.stringify(job));
  await app.namespace.navigate('system');
  const resume=[...document.querySelectorAll('main button')].find(n=>n.textContent==='Review saved preview');
  assert.ok(resume,'Completed optimization must be resumable without another model call');
  resume.click();
  await until(()=>document.querySelector('main').textContent.includes('Review optimization'),'resume saved proposal');
  [...document.querySelectorAll('main button')].find(n=>n.textContent==='Apply').click();
  await until(()=>document.querySelector('dialog[open]'),'apply confirmation');
  [...document.querySelectorAll('dialog button')].find(n=>n.textContent==='Confirm').click();
  await until(()=>document.querySelector('main').textContent.includes('Upload character'),'proposal applied');
  const updated=await app.namespace.api('/characters/Alice.png');
  assert.equal(updated.fields.description,'A thoughtful companion with clear motivations and consistent habits.');
  assert.ok(scrollResets>0,'Page navigation resets the old scroll position');
  const usageModule=await moduleFor('usage.js');
  const overview=usageModule.namespace.usageOverview({totals:{calls:2,complete_calls:1,total_tokens:123456789,input_tokens:123450000,output_tokens:6789,cached_tokens:null,reasoning_tokens:null,failed_calls:1}});
  const totalValue=overview.querySelector('.stat-value');
  assert.equal(totalValue.getAttribute('title'),new Intl.NumberFormat().format(123456789),'Large token figures retain the exact count');
  assert.ok(totalValue.textContent.length<10,'Large metrics remain readable on narrow cards');
  assert.ok(overview.querySelector('.usage-exact'),'Exact counts remain accessible without hovering');
  let finishOld,finishNew;
  app.namespace.registerPage('models','Models',()=>new Promise(resolve=>{finishOld=resolve;}));
  app.namespace.registerPage('usage','Usage',()=>new Promise(resolve=>{finishNew=resolve;}));
  const oldNavigation=app.namespace.navigate('models');await until(()=>finishOld,'old navigation started');
  const newNavigation=app.namespace.navigate('usage');await until(()=>finishNew,'new navigation started');
  const oldView=document.createElement('div');oldView.textContent='Stale view';finishOld(oldView);await oldNavigation;
  assert.equal(document.querySelector('main').getAttribute('aria-busy'),'true','Stale navigation cannot clear current loading state');
  const newView=document.createElement('div');newView.textContent='Current view';finishNew(newView);await newNavigation;
  assert.equal(document.querySelector('main').textContent,'Current view','Only the latest page is committed');
  assert.equal(errors.length,0,errors.map(e=>e.message).join('\n'));
  console.log('mutations=5 passed; stale-session=blocked; optimizer-resume=passed; pages=10 passed; browser-errors=0');
} finally {
  dom.window.close();lines.close();child.kill('SIGTERM');
}
