import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

function room(overrides={}) {
  return {
    session:{session_id:'story-one',title:'The lighthouse',character_file:'Mira.png',mode:'normal'},
    session_id:'story-one',revision:'reviewed-revision',mutable:true,lifecycle:'open',phase:'rising_action',
    scene:{scene_id:'scene-one',thread_id:'thread-one',viewpoint:'Mira',pov:'third_person'},
    direction:'Search the quiet tower.',direction_status:'active',narrative_current:true,scope:'next_scene',objective:'Find the missing keeper.',degraded:false,
    threads:[{thread_id:'thread-one',title:'The missing keeper',status:'active'}],
    arcs:[{arc_id:'arc-one',title:'The beacon',status:'active',phase:'rising_action',importance:'major',summary:'The beacon is dark.',open_questions:[],guidance:''}],
    ending:{mode:'open_ended',lifecycle:'open',goal:'',editable:true,require_confirmation:true,reason:'',
      alternate_available:false,checkpoint_id:'',has_resolution:false,has_epilogue:false,delivery_pending:false,
      recovery_needed:false,error:false,history:[]},
    history:[],cadence:'adaptive',settings:{director_cadence_mode:'adaptive',director_fixed_interval:6},
    director_model:'fixture::director',director_reasoning:1024,...overrides,
  };
}

function trackers(overrides={}) {
  return {session:{session_id:'story-one',title:'The lighthouse'},last_source_rowid:17,last_updated_at:1791244800,pending:false,
    relationships:[],agendas:[],inventory:[],skills:[],conditions:[],factions:[],quests:[],tasks:[],checks:[],...overrides};
}

function namedButton(document,name) {
  const control=[...document.querySelectorAll('button')].find(node=>node.textContent.trim()===name);
  assert.ok(control,'Button is available: '+name);return control;
}

function namedInput(document,name) {
  const label=[...document.querySelectorAll('label')].find(node=>node.textContent===name);
  assert.ok(label,'Field is available: '+name);return document.getElementById(label.htmlFor);
}

async function confirm(document) {
  await until(()=>document.querySelector('dialog[open]'),'operation confirmation');
  namedButton(document,'Confirm').click();
}

test('viewing a saved ending cannot show another story selected after the page loaded',async t=>{
  const state={session:null},requests=[],data=room();data.ending.has_epilogue=true;
  const page=await createPage('director.js',{state,api:async path=>{
    if(path==='/director')return data;
    const request=new URL(path,'http://fixture');
    assert.equal(request.pathname,'/director/ending');requests.push(request);
    const requested=request.searchParams.get('session_id');
    if(requested&&requested!==state.session.session_id)throw Object.assign(new Error('The active session changed.'),{status:409});
    return {epilogue:'Only the new story epilogue.'};
  }});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.director());
  state.session={session_id:'other-story',title:'New current story'};
  const view=namedButton(page.document,'View ending');view.click();
  await until(()=>requests.length===1&&!view.hasAttribute('aria-busy'),'saved ending read finishes');
  assert.equal(page.document.querySelector('.saved-ending'),null,'A stale page must not append another story’s prose');
  assert.equal(requests[0].searchParams.get('session_id'),'story-one');
  assert.match(page.document.querySelector('#notice[role="alert"]')?.textContent||'',/session changed.*Refresh/);
  assert.match(page.document.querySelector('.session-context').textContent,/The lighthouse/);
});

test('a pending Director confirmation cannot submit after another mutation replaces its view',async t=>{
  let finish,reads=0,jobs=0;
  const page=await createPage('director.js',{api:async(path,{method='GET'}={})=>{
    if(method==='GET')return ++reads===1?room():room({session:{session_id:'story-two',title:'New current story'}});
    return new Promise(resolve=>{finish=resolve;});
  },runJob:async()=>{jobs++;return {message:'Reassessed'};}});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.director());
  namedButton(page.document,'Save objective').click();await until(()=>finish,'save starts');
  const reassess=namedButton(page.document,'Reassess now');reassess.click();await until(()=>page.document.querySelector('dialog[open]'),'confirmation opens');
  finish({saved:true});await until(()=>page.document.querySelector('.session-context').textContent.includes('New current story'),'new view loaded');
  namedButton(page.document,'Confirm').click();await until(()=>!reassess.hasAttribute('aria-busy'),'retired confirmation finishes');
  assert.equal(jobs,0,'A confirmation from the retired form cannot start a new operation');
});

test('a rejected reassessment retains the draft, session scope and visible failure',async t=>{
  let page;const jobs=[],state={session:null};
  page=await createPage('director.js',{state,api:async path=>{assert.equal(path,'/director');return room();},
    runJob:async(path,body,target)=>{
      jobs.push({path,body});target.replaceChildren(page.document.createElement('progress'));
      throw new Error('The Director provider is unavailable.');
    }});
  t.after(page.close);
  page.document.querySelector('main').append(await page.pages.director());
  const draft=namedInput(page.document,'Persistent objective');draft.value='Keep my unsaved plan.';
  state.session={session_id:'different-story'};
  const reassess=namedButton(page.document,'Reassess now');reassess.click();await confirm(page.document);
  await until(()=>jobs.length===1&&!reassess.disabled,'reassessment failure');
  assert.equal(draft.isConnected,true,'The operation must not replace the editor');
  assert.equal(draft.value,'Keep my unsaved plan.');
  assert.equal(jobs[0].body.session_id,'story-one');assert.equal(jobs[0].body.revision,'reviewed-revision');
  assert.match(page.document.querySelector('.feedback[role="alert"]')?.textContent||'',/Director provider is unavailable/);
  assert.match(page.document.querySelector('.session-context')?.textContent||'',/The lighthouse/);
  reassess.click();await confirm(page.document);
  await until(()=>jobs.length===2&&!reassess.disabled,'explicit retry after known failure');
});

test('an uncertain operation routes to Operations without submitting another job',async t=>{
  let page,jobs=0;
  page=await createPage('director.js',{api:async()=>room(),runJob:async(_path,_body,target)=>{
    jobs++;target.replaceChildren(page.document.createElement('progress'));
    throw Object.assign(new Error('Connection interrupted while checking the operation.'),{uncertain:true,jobId:'operation-7'});
  }});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.director());
  const draft=namedInput(page.document,'Next-scene direction');draft.value='Keep this draft.';
  const reassess=namedButton(page.document,'Reassess now');reassess.click();await confirm(page.document);
  await until(()=>jobs===1&&!reassess.hasAttribute('aria-busy'),'uncertain operation state');
  assert.equal(draft.isConnected,true);assert.equal(draft.value,'Keep this draft.');
  assert.match(page.document.querySelector('.feedback[role="alert"]')?.textContent||'',/operation-7/);
  namedButton(page.document,'Open Operations').click();
  await until(()=>page.navigations.includes('system'),'Operations navigation');
  assert.equal(reassess.getAttribute('aria-disabled'),'true');
  reassess.click();
  assert.equal(jobs,1,'An unknown result cannot invite another provider call');
  assert.equal(page.document.querySelector('dialog[open]'),null);
});

test('a completed reassessment followed by a failed refresh is still reported as completed',async t=>{
  let reads=0,jobs=0;
  const page=await createPage('director.js',{api:async()=>{if(++reads>1)throw new Error('Read connection unavailable.');return room();},
    runJob:async()=>{jobs++;return {message:'Direction reassessed.'};}});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.director());
  const draft=namedInput(page.document,'Persistent objective');draft.value='Do not discard this draft.';
  const reassess=namedButton(page.document,'Reassess now');reassess.click();await confirm(page.document);
  await until(()=>reads===2&&!reassess.hasAttribute('aria-busy'),'failed refresh after completion');
  assert.equal(draft.isConnected,true);assert.equal(draft.value,'Do not discard this draft.');
  const message=page.document.querySelector('.feedback')?.textContent||'';
  assert.match(message,/Direction reassessed/);assert.match(message,/refresh|reload/i);
  assert.equal(reassess.getAttribute('aria-disabled'),'true');reassess.click();assert.equal(jobs,1);
});

test('Director decisions retain real outcomes and reasons without invented dates or markup',async t=>{
  const page=await createPage('director.js',{api:async()=>room({history:[
    {id:'one',source:'manual',result:'accepted',direction:'Keep <b>the beacon</b> quiet.',reason:'The keeper is listening.'},
    {id:'two',source:'manual',result:'rejected',direction:'',reason:'The thread is already resolved.'},
    {id:'three',source:'manual',result:'failed',direction:'',reason:'Provider unavailable.'},
  ]})});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.director());
  const history=page.document.querySelector('[aria-label="Director decisions"]');assert.ok(history);
  assert.deepEqual([...history.querySelectorAll('.decision-status')].map(node=>node.textContent),['Accepted','Rejected','Failed']);
  for(const text of ['manual','Keep <b>the beacon</b> quiet.','The keeper is listening.','The thread is already resolved.','Provider unavailable.'])assert.ok(history.textContent.includes(text));
  assert.equal(history.querySelector('b,script,time'),null);
  assert.match(page.document.querySelector('main').textContent,/hidden plans/);
  assert.ok(history.compareDocumentPosition(namedInput(page.document,'Persistent objective'))&page.window.Node.DOCUMENT_POSITION_FOLLOWING,
    'The decision record is reachable before the long controls');
});

test('tracker categories navigate saved facts without mutating or revealing private fields',async t=>{
  const requests=[];
  const page=await createPage('trackers.js',{api:async(path,options={})=>{
    requests.push({path,method:options.method||'GET'});return trackers({pending:true,
      inventory:[{name:'Brass <b>key</b>',domain:'stealth',modifier:1}],
      tasks:[{name:'find-keeper',objective:'Reach the tower',status:'active',stage:'Courtyard',progress_current:1,progress_target:3,
        completed_steps:['Cross the bridge'],pending_steps:['Open the gate'],complications:['Rain'],consequence:'The trail is visible.',last_check_key:'private-check-key'}],
    });
  }});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.trackers());
  const navigation=page.document.querySelector('nav[aria-label="Saved tracker categories"]');assert.ok(navigation);
  for(const key of ['relationships','agendas','inventory','skills','conditions','factions','quests','tasks','checks']) {
    const link=navigation.querySelector('[href="#tracker-'+key+'"]');assert.ok(link,'Category remains reachable: '+key);
    assert.ok(page.document.getElementById('tracker-'+key));
  }
  navigation.querySelector('[href="#tracker-tasks"]').click();
  assert.deepEqual(requests,[{path:'/trackers',method:'GET'}]);
  assert.equal(page.document.querySelector('main input,main textarea,main select,main b,main script'),null);
  const text=page.document.querySelector('main').textContent;
  for(const expected of ['The lighthouse','Catching up','Brass <b>key</b>','Cross the bridge','Open the gate','The trail is visible.'])assert.ok(text.includes(expected));
  assert.ok(!text.includes('private-check-key'));
  assert.equal(page.document.querySelector('time').dateTime,'2026-10-06T00:00:00.000Z');
});

test('an empty tracker view does not invent a story or freshness date',async t=>{
  const page=await createPage('trackers.js',{api:async()=>trackers({session:null,last_source_rowid:0,last_updated_at:null})});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.trackers());
  assert.match(page.document.querySelector('main').textContent,/No story selected/);
  assert.equal(page.document.querySelector('time'),null);
  assert.equal(page.document.querySelector('main input,main textarea,main select'),null);
});

test('inactive direction is clearly historical while its draft stays editable',async t=>{
  const page=await createPage('director.js',{api:async()=>room({direction_status:'inactive',narrative_current:false})});
  t.after(page.close);page.document.querySelector('main').append(await page.pages.director());
  const overview=page.document.querySelector('.director-overview');
  assert.match(overview.textContent,/Last accepted direction \(inactive\)/);
  assert.ok(!overview.textContent.includes('Current direction'));
  assert.match(overview.textContent,/not currently used/);
  assert.match(overview.textContent,/Narrative continuity is not current/);
  assert.equal(namedInput(page.document,'Next-scene direction').value,'Search the quiet tower.');
  assert.equal(namedInput(page.document,'Persistent objective').value,'Find the missing keeper.');
});
