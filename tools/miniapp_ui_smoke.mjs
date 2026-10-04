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
assert.equal(initialDocument.querySelector('#more-menu'),null,'Management is a full page, not a navigation sheet');
const context=dom.getInternalVMContext(),errors=[],expectedFailures=new Set(),observedFailures=[];
let portraitMode='success',forcedFailure='',scrollResets=0;
let summaryScenario=null,memoryStatusScenario=null,contextStatusScenario=null,memoryDetailScenario=null,memoryDetailRequests=0;
const fixtureRequestTimes=[];
async function reserveFixtureCapacity(count) {
  // Keep real integration phases below the unchanged 120/minute server limit.
  // Response times conservatively follow admission; reserve extra polling headroom.
  assert.ok(Number.isInteger(count)&&count>0&&count<=100);
  for(;;) {
    const now=performance.now();
    while(fixtureRequestTimes.length&&fixtureRequestTimes[0]<=now-61000)fixtureRequestTimes.shift();
    if(fixtureRequestTimes.length+count<=100)return;
    await new Promise(resolve=>setTimeout(resolve,Math.max(1,fixtureRequestTimes[0]+61000-now)));
  }
}

const memoryThresholds={warning:262144,tracing:327680,capture:393216};
const memorySummaries={
  disabled:{enabled:false,state:'disabled',rss_kib:null,thresholds_kib:memoryThresholds,report_count:0,latest_incident:null},
  armed:{enabled:true,state:'armed',rss_kib:200*1024,thresholds_kib:memoryThresholds,report_count:0,latest_incident:null},
  warned:{enabled:true,state:'warned',rss_kib:280*1024,thresholds_kib:memoryThresholds,report_count:0,latest_incident:null},
  tracing:{enabled:true,state:'tracing',rss_kib:340*1024,thresholds_kib:memoryThresholds,report_count:0,latest_incident:null},
  captured:{enabled:true,state:'captured',rss_kib:400*1024,thresholds_kib:memoryThresholds,report_count:2,latest_incident:{timestamp_utc:'2026-10-01T03:00:00+00:00',rss_kib:400*1024}},
};
dom.window.scrollTo=()=>{scrollResets++;};
process.on('unhandledRejection',error=>errors.push(error));
dom.window.Telegram={WebApp:{initData:ready.initData,initDataUnsafe:{start_param:'dashboard'},ready(){},expand(){},BackButton:{onClick(){},hide(){},show(){}}}};
dom.window.AbortSignal=globalThis.AbortSignal;
dom.window.URL.createObjectURL=()=> 'blob:test-fixture';
dom.window.URL.revokeObjectURL=()=>{};
dom.window.fetch=async (path,options)=>{
  assert.ok(String(path).startsWith('/api/v1/'),'Only local API requests are permitted');
  if(String(path)===forcedFailure)return new Response(JSON.stringify({error:{message:'forced optional failure'}}),{status:503,headers:{'Content-Type':'application/json'}});
  if(/^\/api\/v1\/characters\/[^/]+\/portrait$/.test(String(path))) {
    return new Response(new Blob([portraitMode==='success'?'portrait':''],{type:'image/png'}),{status:200,headers:{'Content-Type':'image/png'}});
  }
  if(String(path)==='/api/v1/memory-diagnostics') {
    memoryDetailRequests++;
    if(memoryDetailScenario==='failure')return new Response(JSON.stringify({error:{message:'forced memory detail failure'}}),{status:503,headers:{'Content-Type':'application/json'}});
    if(memoryDetailScenario)return new Response(JSON.stringify(memoryDetailScenario),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(summaryScenario&&String(path)==='/api/v1/memory/summary/generate') {
    assert.equal(options.method,'POST');
    const body=JSON.parse(options.body);
    assert.equal(body.session_id,summaryScenario.sessionId,'Summary regeneration keeps the rendered session scope');
    assert.ok(body.operation_id,'Summary regeneration keeps its durable operation identity');
    summaryScenario.generated=true;
    return new Response(JSON.stringify({id:'summary-'+summaryScenario.name,state:'succeeded',result:{
      summary:summaryScenario.saved,summary_through:summaryScenario.covered,complete:summaryScenario.complete,
    }}),{status:200,headers:{'Content-Type':'application/json'}});
  }
  const response=await fetch(ready.url+path,options);
  fixtureRequestTimes.push(performance.now());
  if((memoryStatusScenario||contextStatusScenario)&&String(path)==='/api/v1/status'&&response.ok) {
    const data=await response.json();
    if(memoryStatusScenario)data.memory_diagnostics=memoryStatusScenario;
    if(contextStatusScenario)data.context_diagnostics=contextStatusScenario;
    return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
  }
  if(summaryScenario&&String(path)==='/api/v1/memory'&&response.ok) {
    const data=await response.json();summaryScenario.reads++;
    summaryScenario.sessionId ||= data.session.session_id;
    data.summary=summaryScenario.generated?summaryScenario.saved:summaryScenario.before;
    data.summary_through=summaryScenario.generated?summaryScenario.covered:40;
    return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
  }
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
  assert.deepEqual([...shellDocument.querySelectorAll('#navigation button')].map(n=>n.textContent.trim()),['Home','Characters','Sessions','Manage','System']);
  assert.equal(shellDocument.querySelectorAll('#navigation svg').length,5,'Every primary destination has a local SVG icon');
  assert.ok(shellDocument.querySelector('.skip-link'),'Keyboard users can skip navigation');
  const sessionsTab=[...shellDocument.querySelectorAll('#navigation button')].find(n=>n.textContent.trim()==='Sessions');
  assert.ok(sessionsTab,'Sessions navigation button exists');sessionsTab.click();
  await until(()=>shellDocument.getElementById('page-title').textContent==='Sessions'&&shellDocument.querySelector('main').textContent.includes('Create session'),'Sessions tab click renders Sessions');
  for(const page of ['dashboard','characters','sessions','manage','advanced','usage','models','personas','worlds','memory','director','npcs','databank','system']) {
    await app.namespace.navigate(page);
    assert.ok(shellDocument.getElementById('page-title').textContent.trim().length>0,page+' updates the compact page title');
    const body=dom.window.document.querySelector('main').textContent;
    assert.ok(!body.includes('Could not load this page'),page+': '+body);
    assert.ok(body.length>30,page+' rendered content');
    if(page==='dashboard') {
      assert.ok(shellDocument.querySelector('.story-card'),'Home leads with the current story');
      assert.ok(shellDocument.querySelector('.dashboard-session'),'Home shows the current session summary');
      const sessionPortrait=shellDocument.querySelector('.dashboard-session .session-portrait');
      assert.ok(sessionPortrait,'Home shows the active character portrait');
      await until(()=>sessionPortrait.getAttribute('src')==='blob:test-fixture','dashboard portrait loaded');
      sessionPortrait.dispatchEvent(new dom.window.Event('load'));
      assert.equal(sessionPortrait.closest('.session-portrait-frame').hidden,false,'Dashboard portrait appears only after loading');
      assert.equal(dom.window.getComputedStyle(sessionPortrait).objectFit,'contain','Dashboard portrait preserves the full image');
      assert.equal(shellDocument.querySelectorAll('.story-status-item').length,3,'Home summarizes persona, world and memory state');
      const statusCopy=shellDocument.querySelector('.story-status-item>span');
      assert.equal(dom.window.getComputedStyle(statusCopy).minWidth,'0px','Status copy may shrink so long values cannot overlap adjacent columns');
      assert.equal(shellDocument.querySelectorAll('.dashboard-shortcuts button').length,4,'Dashboard exposes four quick actions');
      assert.ok(shellDocument.querySelector('.recent-stories'),'Home includes recent stories');
      assert.deepEqual([...shellDocument.querySelectorAll('.dashboard-health [data-health-icon]')].map(node=>node.dataset.healthIcon),['system','telegram','database','models','memory'],'Dashboard Bridge health labels all five status cards with local icons');
      assert.ok(shellDocument.querySelector('.dashboard-health').textContent.includes('Context'),'Home Bridge health includes context diagnostics');
      assert.ok(shellDocument.querySelector('.dashboard-health').textContent.includes('Memory'),'Home Bridge health includes memory diagnostics');
      assert.ok(shellDocument.querySelector('.dashboard-health').textContent.includes('Disabled'),'Disabled memory diagnostics are labeled');
      assert.equal(memoryDetailRequests,0,'Home never fetches detailed memory diagnostics');
      portraitMode='empty';
    }
    if(page==='manage') {
      assert.deepEqual([...shellDocument.querySelectorAll('.manage-link')].map(node=>node.dataset.page),['models','director','personas','worlds','generation','memory','npcs','databank','advanced'],'Manage exposes unique design destinations in order');
      assert.deepEqual([...shellDocument.querySelectorAll('.manage-section-title')].map(node=>node.textContent.trim()),['Story setup','Knowledge','Advanced']);
      const generation=[...shellDocument.querySelectorAll('.manage-link')].find(node=>node.textContent.includes('Generation'));
      generation.click();
      await until(()=>shellDocument.getElementById('page-title').textContent==='Models','Generation opens the existing generation controls');
    }
    if(page==='advanced') {
      assert.deepEqual([...shellDocument.querySelectorAll('.manage-link')].map(node=>node.dataset.page),['usage'],'Advanced settings keeps Usage reachable');
    }
    if(page==='director') {
      assert.ok(shellDocument.querySelector('.director-room-page'),'Director Room renders its own private page');
      assert.ok(body.includes('hidden plans'),'Director Room distinguishes plans from story facts');
      assert.ok([...shellDocument.querySelectorAll('main button')].some(node=>node.textContent==='Save objective'),'Persistent objective has an explicit save action');
      const labels=[...shellDocument.querySelectorAll('main label')].map(node=>node.textContent);
      assert.ok(labels.includes('Reassessment cadence'),'Adaptive and fixed cadence controls are visible');
      assert.ok(labels.includes('Director reasoning budget (0–32000)'),'Director reasoning stays independently configurable');
    }
    if(page==='npcs') {
      assert.ok(shellDocument.querySelector('.npc-bank-page'),'NPC Bank page renders');
      assert.ok(body.includes('NPC Bank'),'NPC Bank labels its page');
    }
    if(page==='usage') {
      assert.ok(shellDocument.querySelector('.usage-dashboard'),'Dedicated usage page renders');
      assert.ok(shellDocument.querySelector('select[aria-label="Usage period"]'),'Period is selectable');
      assert.ok(shellDocument.querySelector('select[aria-label="Usage scope"]'),'Private session scope is selectable');
      assert.ok(body.includes('No recorded usage'),'Empty history is not presented as zero billed tokens');
      assert.ok(body.includes('provider-reported'),'Usage describes its measurement source');
    }
    if(page==='system') {
      assert.deepEqual([...shellDocument.querySelectorAll('main [data-health-icon]')].map(node=>node.dataset.healthIcon),['system','telegram','database','models','memory'],'System status labels all five health cards with local icons');
      const review=[...shellDocument.querySelectorAll('main button')].find(n=>n.textContent==='Review latest release');
      assert.ok(review,'System exposes release review');review.click();
      await until(()=>shellDocument.querySelector('main').textContent.includes('Already latest'),'already-latest review state');
      assert.equal([...shellDocument.querySelectorAll('main button')].some(n=>n.textContent==='Install reviewed release'),false,'Already-current release cannot be installed again');
    }
    if(page==='characters') {
      assert.ok(shellDocument.querySelector('.character-card .portrait-frame'),'Character cards use a portrait-first visual layout');
      assert.ok(shellDocument.querySelector('.character-page-header'),'Characters exposes the compact concept header');
      assert.ok(shellDocument.querySelector('.character-card details.character-actions-menu'),'Secondary character actions use a compact menu');
      assert.ok(shellDocument.querySelector('.character-card button.character-use'),'Use remains the primary character action');
      assert.ok([...shellDocument.querySelectorAll('main button')].some(n=>n.textContent==='Restore backups'),'Characters exposes verified backup restore');
      const uploadInput=shellDocument.querySelector('input.character-file-input[type="file"]');
      assert.ok(uploadInput&&uploadInput.isConnected,'Add character keeps its upload input connected to the page');
      assert.equal(uploadInput.hidden,true,'Upload input stays visually hidden behind the add action');
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
  await reserveFixtureCapacity(10);
  await app.namespace.navigate('director');
  const objectiveLabel=[...shellDocument.querySelectorAll('main label')].find(node=>node.textContent==='Persistent objective');
  assert.ok(objectiveLabel,'Director Room exposes a persistent objective input');
  const objective= shellDocument.getElementById(objectiveLabel.htmlFor);
  objective.value='<b>Keep the lighthouse safe.</b>';
  const directorSession=app.namespace.state.session.session_id;
  app.namespace.state.session={...app.namespace.state.session,session_id:'other-view-cannot-retarget-director'};
  const saveObjective=[...shellDocument.querySelectorAll('main button')].find(node=>node.textContent==='Save objective');
  saveObjective.click();await until(()=>!saveObjective.disabled,'Director objective saved');
  const savedDirector=await app.namespace.api('/director');
  assert.equal(savedDirector.session.session_id,directorSession,'Director mutation keeps the rendered session scope');
  assert.equal(savedDirector.objective,objective.value,'Director objective uses the shared canonical service');
  assert.equal(shellDocument.querySelector('.director-room-page b'),null,'User-authored planning text is never inserted as HTML');
  assert.equal(shellDocument.getElementById('notice').textContent,'Director objective saved.');
  await reserveFixtureCapacity(14);
  for(const [mode,confirmation] of [['closed_story','true'],['open_ended','false']]) {
    await app.namespace.navigate('director');
    const endingLabel=[...shellDocument.querySelectorAll('main label')].find(node=>node.textContent==='Story ending');
    const finaleLabel=[...shellDocument.querySelectorAll('main label')].find(node=>node.textContent==='Finale entry');
    assert.ok(endingLabel&&finaleLabel,'Director Room exposes ending mode and finale consent');
    shellDocument.getElementById(endingLabel.htmlFor).value=mode;
    shellDocument.getElementById(finaleLabel.htmlFor).value=confirmation;
    app.namespace.state.session={...app.namespace.state.session,session_id:'different-view-must-not-retarget-ending'};
    const saveEnding=[...shellDocument.querySelectorAll('main button')].find(node=>node.textContent==='Save ending settings');
    saveEnding.click();await until(()=>!saveEnding.disabled,'Ending preference saved');
    const savedEnding=await app.namespace.api('/director');
    assert.equal(savedEnding.session.session_id,directorSession,'Ending preferences stay with the rendered session');
    assert.equal(savedEnding.ending.mode,mode);
    assert.equal(savedEnding.ending.require_confirmation,confirmation==='true');
    assert.equal(shellDocument.getElementById('notice').textContent,'Ending settings saved for this story.');
  }
  console.log('ending-controls=mode,confirmation,rendered-session-scope passed');
  await app.namespace.navigate('dashboard');
  await until(()=>shellDocument.querySelector('.dashboard-session .session-portrait-frame')?.hidden===true,'dashboard missing portrait fallback');
  assert.ok(shellDocument.querySelector('.dashboard-session').textContent.includes('Default session'),'Dashboard text remains when its portrait is unavailable');
  forcedFailure='/api/v1/personas';
  await app.namespace.navigate('dashboard');
  assert.ok(shellDocument.querySelector('.story-card'),'An optional status failure cannot blank Home');
  assert.ok([...shellDocument.querySelectorAll('.story-status-item')].some(node=>node.textContent.includes('Unavailable')),'Unavailable optional status is labeled');
  forcedFailure='';
  contextStatusScenario={
    source:'provider-model',window_tokens:32768,output_reserve_tokens:4096,safety_margin_tokens:656,
    budget_tokens:28016,final_tokens:23450,usage_percent:84,compacted:true,dropped_history:4,
    trimmed_components:['memory','summary'],memory_trimmed:true,rag_trimmed:false,npc_trimmed:false,summary_trimmed:true,
  };
  await app.namespace.navigate('dashboard');
  let contextText=shellDocument.querySelector('.dashboard-health [data-health-icon="models"]').closest('.card').textContent;
  assert.ok(contextText.includes('23,450 / 28,016 input tokens (84%)'),'Home shows used and usable input context with percentage');
  assert.ok(contextText.includes('Compacted'),'Home labels compacted context explicitly');
  assert.ok(contextText.includes('memory, summary'),'Home identifies trimmed context components');
  await app.namespace.navigate('system');
  contextText=shellDocument.querySelector('main [data-health-icon="models"]').closest('.card').textContent;
  assert.ok(contextText.includes('23,450 / 28,016 input tokens (84%)'),'System shows used and usable input context with percentage');
  assert.ok(contextText.includes('Compacted'),'System labels compacted context explicitly');
  contextStatusScenario=null;
  for(const [name,label,rssText] of [
    ['disabled','Disabled','Not sampled'],
    ['armed','Armed','200 MiB'],
    ['warned','Warned','280 MiB'],
    ['tracing','Tracing','340 MiB'],
    ['captured','Captured','400 MiB'],
  ]) {
    memoryStatusScenario=memorySummaries[name];
    const detailBefore=memoryDetailRequests;
    await app.namespace.navigate('dashboard');
    const healthText=shellDocument.querySelector('.dashboard-health').textContent;
    assert.ok(healthText.includes(label),'Home renders '+name+' memory state');
    assert.ok(healthText.includes(rssText),'Home renders '+name+' memory RSS');
    if(name==='captured')assert.ok(healthText.includes('Last incident'),'Captured Home memory state shows latest incident time');
    assert.equal(memoryDetailRequests,detailBefore,'Home '+name+' state does not request detailed diagnostics');
  }
  memoryStatusScenario={...memorySummaries.disabled,report_count:2,latest_incident:memorySummaries.captured.latest_incident};
  let retainedDetailBefore=memoryDetailRequests;
  await app.namespace.navigate('dashboard');
  const disabledRetainedText=shellDocument.querySelector('.dashboard-health').textContent;
  assert.ok(disabledRetainedText.includes('2 retained reports'),'Disabled Home memory state preserves retained report count');
  assert.ok(disabledRetainedText.includes('Last incident'),'Disabled Home memory state preserves latest incident time');
  assert.equal(memoryDetailRequests,retainedDetailBefore,'Disabled Home retained history still uses status summary only');
  memoryStatusScenario=memorySummaries.disabled;
  memoryDetailScenario={summary:memorySummaries.disabled,reports:[]};
  let detailBefore=memoryDetailRequests;
  await app.namespace.navigate('system');
  await until(()=>memoryDetailRequests===detailBefore+1,'System fetches detailed memory diagnostics');
  assert.ok(shellDocument.querySelector('main').textContent.includes('Memory diagnostics'),'System renders memory diagnostics detail card');
  assert.ok(shellDocument.querySelector('main').textContent.includes('No memory incident reports'),'System renders zero-report memory state');

  const makeReport=(ratio,path='/home/private/work/bridge/generation.py')=>({
    timestamp_utc:'2026-10-01T03:00:00+00:00',rss_kib:400*1024,
    smaps_kib:{Rss:400*1024,Private_Dirty:300*1024,Anonymous:280*1024},
    traced_current_bytes:Math.round(400*1024*1024*ratio),traced_peak_bytes:300*1024*1024,
    thread_count:11,safe_counters:{jobs:2},
    top_sites:Array.from({length:12},(_,index)=>({file:index===0?path:'bridge/provider_port.py',line:100+index,size_bytes:1000-index,blocks:2})),
  });
  for(const [ratio,hint] of [
    [0.70,'Python-traced allocations are a significant share of process memory.'],
    [0.20,'Native or otherwise untraced memory appears significant.'],
    [0.45,'The report is mixed; inspect allocation sites and process-memory totals.'],
  ]) {
    const report=makeReport(ratio);
    memoryStatusScenario={...memorySummaries.captured,report_count:1};
    memoryDetailScenario={summary:memoryStatusScenario,reports:[report]};
    detailBefore=memoryDetailRequests;
    await app.namespace.navigate('system');
    await until(()=>memoryDetailRequests===detailBefore+1,'System refreshes detailed memory diagnostics');
    const systemText=shellDocument.querySelector('main').textContent;
    assert.ok(systemText.includes('400 MiB'),'System renders incident RSS');
    assert.ok(systemText.includes('11 threads'),'System renders thread count');
    assert.ok(systemText.includes(hint),'System renders the expected memory interpretation hint');
    const allocationDetails=[...shellDocument.querySelectorAll('main details')].find(node=>node.textContent.includes('Top Python allocations'));
    assert.ok(allocationDetails,'System exposes collapsed top Python allocations');
    assert.ok(allocationDetails.querySelectorAll('li').length<=10,'System caps allocation sites at ten');
    assert.ok(!systemText.includes('/home/private'),'System never renders an absolute private path');
  }

  const unavailableTraceReport=makeReport(0.45);
  unavailableTraceReport.traced_current_bytes=null;
  memoryStatusScenario={...memorySummaries.captured,report_count:1};
  memoryDetailScenario={summary:memoryStatusScenario,reports:[unavailableTraceReport]};
  detailBefore=memoryDetailRequests;
  await app.namespace.navigate('system');
  await until(()=>memoryDetailRequests===detailBefore+1,'System renders unavailable traced-memory diagnostics');
  const unavailableTraceText=shellDocument.querySelector('main').textContent;
  assert.ok(unavailableTraceText.includes('The report is mixed; inspect allocation sites and process-memory totals.'),'Missing traced-current memory yields a mixed interpretation');
  assert.ok(!unavailableTraceText.includes('Native or otherwise untraced memory appears significant.'),'Missing traced-current memory is not misclassified as native/untraced');

  memoryStatusScenario=memorySummaries.disabled;
  memoryDetailScenario='failure';
  detailBefore=memoryDetailRequests;
  await app.namespace.navigate('system');
  await until(()=>memoryDetailRequests===detailBefore+1,'System attempts optional detailed memory diagnostics');
  assert.ok(shellDocument.querySelector('main').textContent.includes('System'),'Detail failure leaves System usable');
  assert.ok(shellDocument.querySelector('main').textContent.includes('Memory diagnostics unavailable'),'Detail failure is labeled without blanking System');
  memoryStatusScenario=null;memoryDetailScenario=null;

  const summaryResults=[];
  for(const scenario of [
    {name:'later-segment-failure',before:'Old complete continuity.',saved:'Completed prefix continuity.',covered:24,complete:false},
    {name:'old-summary-fallback',before:'Preserved old continuity.',saved:'Preserved old continuity.',covered:40,complete:false},
    {name:'complete',before:'Earlier continuity.',saved:'Fully regenerated continuity.',covered:40,complete:true},
  ]) {
    summaryScenario={...scenario,generated:false,reads:0,sessionId:''};
    await app.namespace.navigate('memory');
    const memoryDocument=dom.window.document;
    const summaryLabel=[...memoryDocument.querySelectorAll('label')].find(n=>n.textContent==='Summary');
    assert.equal(memoryDocument.getElementById(summaryLabel.htmlFor).value,scenario.before);
    memoryDocument.getElementById('notice').textContent='';memoryDocument.getElementById('notice').hidden=true;
    // A different view's state cannot retarget the already-rendered summary action.
    app.namespace.state.session={...app.namespace.state.session,session_id:'different-view-session'};
    const regenerate=[...memoryDocument.querySelectorAll('main button')].find(n=>n.textContent==='Regenerate with Utility');
    regenerate.click();await until(()=>memoryDocument.querySelector('dialog[open]'),'summary '+scenario.name+' confirmation');
    [...memoryDocument.querySelectorAll('dialog button')].find(n=>n.textContent==='Confirm').click();
    await until(()=>!regenerate.disabled,'summary '+scenario.name+' regeneration finished');
    assert.equal(summaryScenario.generated,true,'Summary '+scenario.name+' submits the requested job');
    assert.ok(summaryScenario.reads>=2,'Summary '+scenario.name+' reloads saved content after regeneration');
    const reloadedLabel=[...memoryDocument.querySelectorAll('label')].find(n=>n.textContent==='Summary');
    assert.equal(memoryDocument.getElementById(reloadedLabel.htmlFor).value,scenario.saved,'Summary '+scenario.name+' displays the saved content');
    const notice=memoryDocument.getElementById('notice');
    summaryResults.push({name:scenario.name,incomplete:!notice.hidden&&/incomplete/i.test(notice.textContent),
      explainsSaved:!notice.hidden&&/preserved|completed work/i.test(notice.textContent)});
  }
  summaryScenario=null;
  assert.deepEqual(summaryResults,[
    {name:'later-segment-failure',incomplete:true,explainsSaved:true},
    {name:'old-summary-fallback',incomplete:true,explainsSaved:true},
    {name:'complete',incomplete:false,explainsSaved:false},
  ],'Incomplete summary regeneration must be reported for partial work and old-summary fallback; complete regeneration retains its normal flow');
  console.log('summary-regeneration=partial-notice,old-fallback-notice,complete-normal; summary-session-scope=preserved');
  // Extra real reads make the rolling-window regression reproducible on fast runners.
  for(let extra=0;extra<12;extra++)await app.namespace.api('/status');
  await reserveFixtureCapacity(60);
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
  const defaultCard=[...document.querySelectorAll('main .card')].find(node=>node.querySelector('h2')?.textContent==='Default session');
  assert.ok(defaultCard,'Inactive Default session card is present');
  const defaultOpen=[...defaultCard.querySelectorAll('button')].find(n=>n.textContent==='Open');
  assert.ok(defaultOpen,'Inactive session exposes Open');defaultOpen.click();
  await until(()=>document.getElementById('page-title').textContent==='Home'&&document.querySelector('main .story-card')?.textContent.includes('Default session'),'Opening inactive session returns Home');
  let opened=await app.namespace.api('/sessions');assert.equal(opened.session.title,'Default session','Open switches the backend active session');
  await app.namespace.navigate('sessions');
  const activeDefaultCard=[...document.querySelectorAll('main .card')].find(node=>node.querySelector('h2')?.textContent==='Default session');
  const activeOpen=[...activeDefaultCard.querySelectorAll('button')].find(n=>n.textContent==='Open');
  activeOpen.click();
  await until(()=>document.getElementById('page-title').textContent==='Home'&&document.querySelector('main .story-card')?.textContent.includes('Default session'),'Opening active session still returns Home');
  opened=await app.namespace.api('/sessions');assert.equal(opened.session.title,'Default session');
  const info=await app.namespace.api('/characters/Alice.png');
  const optimization=await app.namespace.runJob(
    '/characters/Alice.png/optimize',
    app.namespace.sessionBody({digest:info.digest,suggestion:'Clarify motivation.'}),
  );
  assert.equal(optimization.kind,'optimize');
  await app.namespace.navigate('system');
  const resume=[...document.querySelectorAll('main button')].find(n=>n.textContent==='Review saved preview');
  assert.ok(resume,'Completed optimization must be resumable without another model call');
  resume.click();
  await until(()=>document.querySelector('main').textContent.includes('Review optimization'),'resume saved proposal');
  [...document.querySelectorAll('main button')].find(n=>n.textContent==='Apply').click();
  await until(()=>document.querySelector('dialog[open]'),'apply confirmation');
  [...document.querySelectorAll('dialog button')].find(n=>n.textContent==='Confirm').click();
  await until(()=>document.querySelector('main .character-page-header'),'proposal applied');
  assert.ok(document.querySelector('main input.character-file-input')?.isConnected,'Upload remains available after applying a proposal');
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
  console.log('mutations=5 passed; stale-session=blocked; optimizer-resume=passed; pages=14 passed; browser-errors=0');
} finally {
  dom.window.close();lines.close();child.kill('SIGTERM');
}
