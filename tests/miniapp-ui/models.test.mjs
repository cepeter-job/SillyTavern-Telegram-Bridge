import test from 'node:test';
import assert from 'node:assert/strict';
import {createPage,until} from './dom.mjs';

const session={session_id:'story-a',title:'The Night Archive',character_file:'Alice.png',model_id:'test::story',mode:'normal'};
const settings={temperature:0.7,top_p:0.9,max_tokens:2048,frequency_penalty:0,presence_penalty:0,reasoning_budget:0,stop_sequences:''};
const models=[{id:'test::story',provider:'test',name:'story'},{id:'test::utility',provider:'test',name:'utility'}];
const restoreUndoMessage='Character backup restored. The previous installed card is now the available backup; restoring again will undo this change.';
function modelApi({configured={story:'test::story',utility:'',director:''},effective={},mutate=async()=>({saved:true}),catalogModels=models}={}) {
  return async(path,options={})=>{
    if(options.method&&options.method!=='GET')return mutate(path,options);
    if(path.startsWith('/models?'))return {models:catalogModels,story:'test::story',utility:'test::story',director:'test::story',configured,session,limit:500,...effective};
    if(path==='/generation')return {settings:{...settings},session};
    if(path==='/generation/presets')return {presets:['Cinematic']};
    throw new Error('Unexpected API: '+path);
  };
}
function labelled(document,text) {
  const label=[...document.querySelectorAll('label')].find(node=>node.textContent.startsWith(text));
  assert.ok(label,'Control is labelled: '+text);return document.getElementById(label.htmlFor);
}
function namedButton(document,text) {
  const control=[...document.querySelectorAll('button')].find(node=>node.textContent===text);
  assert.ok(control,'Action is available: '+text);return control;
}
async function renderModels(options={}) {
  const page=await createPage('models.js',options);page.document.getElementById('content').append(await page.pages.models());return page;
}
async function press(document,text) {
  const control=namedButton(document,text);control.click();await until(()=>control.getAttribute('aria-busy')!=='true',text+' completed');return control;
}

test('inherited model roles retain an empty saved selection and identify the effective model',async t=>{
  const page=await renderModels({api:modelApi()});t.after(page.close);
  assert.equal(labelled(page.document,'Utility model').value,'');
  assert.equal(labelled(page.document,'Director model').value,'');
  const utility=page.document.querySelector('[data-role="utility"]');
  const director=page.document.querySelector('[data-role="director"]');
  assert.match(utility.textContent,/Inherit Story/);
  assert.match(utility.querySelector('.model-effective').textContent,/test::story/);
  assert.match(director.textContent,/Utility.*Story/);
  assert.match(director.querySelector('.model-effective').textContent,/test::story/);
  assert.match(page.document.querySelector('.session-context').textContent,/The Night Archive/);
});

test('an explicit role equal to Story stays explicit and a filtered current model stays selectable',async t=>{
  const page=await renderModels({api:modelApi({configured:{story:'test::story',utility:'test::story',director:'old::director'},effective:{director:'test::story'},catalogModels:[]})});t.after(page.close);
  assert.equal(labelled(page.document,'Utility model').value,'test::story');
  const director=labelled(page.document,'Director model');assert.equal(director.value,'old::director');
  assert.ok([...director.options].some(option=>option.value==='old::director'));
  const row=page.document.querySelector('[data-role="director"]');
  assert.match(row.textContent,/old::director/);
  assert.match(row.querySelector('.model-effective').textContent,/test::story/);
});

test('models never combine role choices and generation settings from different active stories',async t=>{
  const source=modelApi();
  const page=await createPage('models.js',{api:async(path,options)=>{
    const response=await source(path,options);
    return path==='/generation'?{...response,session:{...session,session_id:'story-b',title:'Another story'}}:response;
  }});t.after(page.close);
  await assert.rejects(page.pages.models(),/story changed.*refresh.*review/i);
});

test('a preset confirmation cannot retarget a new story loaded by a pending search',async t=>{
  const writes=[],pending=[],other={...session,session_id:'story-b',title:'The Sun Archive'};
  let phase='initial';const source=modelApi({mutate:async(path,options)=>{writes.push({path,...options});return {saved:true};}});
  const page=await renderModels({api:async(path,options={})=>{
    const response=await source(path,options);
    if(!options.method&&(path.startsWith('/models?')||path==='/generation')) {
      if(phase==='pending')return new Promise(resolve=>pending.push(()=>resolve({...response,session:other})));
      if(phase==='other')return {...response,session:other};
    }
    return response;
  }});t.after(page.close);
  labelled(page.document,'Save current server settings as').value='Cinematic';
  labelled(page.document,'Search configured models').value='slow';phase='pending';
  namedButton(page.document,'Search').click();await until(()=>pending.length===2,'pending model search');
  const save=namedButton(page.document,'Save preset');save.click();
  await until(()=>page.document.querySelector('dialog[open]'),'old story preset confirmation');
  phase='other';pending.forEach(resolve=>resolve());
  await until(()=>page.document.querySelector('.session-context')?.textContent.includes('The Sun Archive'),'new story search result');
  namedButton(page.document,'Confirm').click();await until(()=>save.getAttribute('aria-busy')!=='true','old confirmation finished');
  assert.equal(writes.length,0,'A confirmation from the retired form cannot write the newly displayed story');
  const error=page.document.querySelector('[role="alert"]');assert.ok(error,'The retired confirmation explains that review is required');
  assert.match(error.textContent,/refresh.*review/i);
});

for(const actionName of ['Save preset','Delete'])test(actionName+' rechecks its pending confirmation after another mutation locks the form',async t=>{
  const writes=[];let resolveSave,completed=false;
  const source=modelApi({mutate:async(path,options)=>{
    writes.push({path,...options});
    if(path==='/models') {await new Promise(resolve=>{resolveSave=resolve;});completed=true;}
    return {saved:true};
  }});
  const page=await renderModels({api:async(path,options={})=>{
    if(!options.method&&completed)throw new Error('Refresh unavailable.');
    return source(path,options);
  }});t.after(page.close);
  const modelSave=namedButton(page.document,'Save Utility');modelSave.click();
  await until(()=>resolveSave,'model save in flight');
  labelled(page.document,'Save current server settings as').value='Cinematic';
  const pendingAction=namedButton(page.document,actionName);pendingAction.click();
  await until(()=>page.document.querySelector('dialog[open]'),'preset confirmation while model save is in flight');
  resolveSave();await until(()=>modelSave.getAttribute('aria-busy')!=='true','model saved and view locked');
  namedButton(page.document,'Confirm').click();await until(()=>pendingAction.getAttribute('aria-busy')!=='true','pending confirmation finished');
  assert.equal(writes.length,1,'The pending confirmation cannot bypass the refresh lock');
  assert.equal(writes[0].path,'/models');
});

test('an invalid generation field prevents the entire save and keeps other edits for correction',async t=>{
  const writes=[];
  const page=await renderModels({api:modelApi({mutate:async(path,options)=>{writes.push({path,...options});return {};}})});t.after(page.close);
  labelled(page.document,'Temperature').value='0.45';labelled(page.document,'Maximum output tokens').value='0';
  await press(page.document,'Save settings');
  assert.equal(writes.length,0);
  assert.equal(labelled(page.document,'Temperature').value,'0.45');
  assert.equal(labelled(page.document,'Maximum output tokens').value,'0');
  const error=page.document.querySelector('[role="alert"]');assert.ok(error,'Validation is reported persistently next to the form');
  assert.match(error.textContent,/maximum output tokens|max tokens/i);
});

test('a rejected generation save retains every field and offers refresh with review using its captured session',async t=>{
  const writes=[],state={session};
  const page=await renderModels({state,api:modelApi({mutate:async(path,options)=>{writes.push({path,...options});const error=new Error('The active session changed.');error.status=409;throw error;}})});t.after(page.close);
  labelled(page.document,'Temperature').value='0.45';
  labelled(page.document,'Top P').value='0.8';
  labelled(page.document,'Maximum output tokens').value='1024';
  labelled(page.document,'Frequency penalty').value='0.2';
  labelled(page.document,'Presence penalty').value='-0.1';
  const reasoning=labelled(page.document,'Reasoning level');reasoning.value='custom';reasoning.dispatchEvent(new page.window.Event('change'));
  labelled(page.document,'Custom reasoning budget').value='12345';
  labelled(page.document,'Stop sequences').value='END\nFIN';
  state.session={...session,session_id:'story-b',title:'A different story'};
  await press(page.document,'Save settings');
  assert.equal(writes.length,1);assert.equal(writes[0].path,'/generation');assert.equal(writes[0].body.session_id,'story-a');
  assert.deepEqual(JSON.parse(JSON.stringify(writes[0].body.settings)),{temperature:0.45,top_p:0.8,max_tokens:1024,frequency_penalty:0.2,presence_penalty:-0.1,reasoning_budget:12345,stop_sequences:'END\nFIN'});
  assert.equal(labelled(page.document,'Temperature').value,'0.45');assert.equal(labelled(page.document,'Custom reasoning budget').value,'12345');
  assert.equal(labelled(page.document,'Stop sequences').value,'END\nFIN');
  const error=page.document.querySelector('[role="alert"]');assert.ok(error,'Save failure remains available for review');
  assert.match(error.textContent,/refresh.*review|review.*refresh/i);
  assert.match(page.document.querySelector('.session-context').textContent,/The Night Archive/);
  assert.ok(namedButton(page.document,'Refresh and review'));
});

for(const scenario of [
  {action:'Save Utility',path:'/models',method:'POST',outcome:/Utility model saved/i,response:{saved:true,target:'utility',model:'test::utility'}},
  {action:'Apply',path:'/generation/presets/Cinematic/apply',method:'POST',outcome:/Preset applied/i,response:{settings}},
  {action:'Delete',path:'/generation/presets/Cinematic',method:'DELETE',outcome:/Preset deleted/i,response:{deleted:true},confirm:true},
  {action:'Save preset',path:'/generation/presets',method:'POST',outcome:/Preset saved/i,response:{saved:true}},
])test(scenario.action+' remains completed when its refresh fails and locks mutations until a successful review',async t=>{
  let completed=false,allowRefresh=false;const writes=[];
  const source=modelApi({mutate:async(path,options)=>{writes.push({path,...options});completed=true;return scenario.response;}});
  const page=await renderModels({api:async(path,options={})=>{
    if(!options.method&&completed&&!allowRefresh)throw new Error('The follow-up refresh is unavailable.');
    return source(path,options);
  }});t.after(page.close);
  labelled(page.document,'Utility model').value='test::utility';
  labelled(page.document,'Temperature').value='0.45';
  labelled(page.document,'Save current server settings as').value='New_preset';
  const action=namedButton(page.document,scenario.action);action.click();
  if(scenario.confirm) {
    await until(()=>page.document.querySelector('dialog[open]'),'preset deletion confirmation');
    namedButton(page.document,'Confirm').click();
  }
  await until(()=>action.getAttribute('aria-busy')!=='true','mutation and failed refresh finished');
  assert.equal(writes.length,1);assert.equal(writes[0].path,scenario.path);assert.equal(writes[0].method,scenario.method);
  const message=page.document.querySelector('.models-page [role="alert"]');assert.ok(message,'Refresh failure is visible beside the retained form');
  assert.match(message.textContent,scenario.outcome);assert.match(message.textContent,/refresh.*review/i);
  assert.doesNotMatch(message.textContent,/try again|could not save changes/i);
  assert.equal(labelled(page.document,'Temperature').value,'0.45');
  assert.equal(labelled(page.document,'Utility model').value,'test::utility');
  assert.equal(labelled(page.document,'Save current server settings as').value,'New_preset');
  for(const label of ['Save Story','Save Utility','Save Director','Save settings','Apply','Delete','Save preset']) {
    const control=namedButton(page.document,label);assert.equal(control.disabled,true,label+' requires a fresh read');
    control.dispatchEvent(new page.window.MouseEvent('click',{bubbles:true}));
  }
  assert.equal(writes.length,1,'No mutation can be replayed from the stale view');
  allowRefresh=true;await press(page.document,'Refresh and review');
  assert.equal(writes.length,1,'Review only reloads server state');
  assert.equal(namedButton(page.document,'Save Utility').disabled,false,'A fresh view permits a new deliberate change');
  assert.equal(labelled(page.document,'Temperature').value,'0.7','The successful refresh presents server settings for review');
});

test('Start new story confirms a new session and preserves the scoped character selection operation',async t=>{
  const writes=[],state={session};let active=session;
  const page=await createPage('characters.js',{state,api:async(path,options={})=>{
    if(path.startsWith('/characters?'))return {session:active,total:1,offset:0,characters:[{filename:'Alice.png',name:'Alice',rank:'S',active:true,digest:'card-digest'}]};
    if(path==='/characters/Alice.png/portrait')return null;
    if(path==='/characters/Alice.png/select') {writes.push(options);active={...session,session_id:'new-story',title:'Alice chat'};return {session:active,message:'New session selected. Use /start in Telegram to begin.'};}
    throw new Error('Unexpected API: '+path);
  }});t.after(page.close);
  page.document.getElementById('content').append(await page.pages.characters());
  const context=page.document.querySelector('.session-context');assert.ok(context,'The new story action identifies the current story');
  assert.match(context.textContent,/The Night Archive/);
  state.session={...session,session_id:'other-story'};
  namedButton(page.document,'Start new story').click();
  await until(()=>page.document.querySelector('dialog[open]'),'new story confirmation');
  assert.match(page.document.querySelector('dialog').textContent,/new.*Alice/i);
  assert.equal(writes.length,0);
  namedButton(page.document,'Confirm').click();
  await until(()=>writes.length===1,'new story selected');
  assert.equal(writes[0].method,'POST');assert.equal(writes[0].body.confirm,true);assert.equal(writes[0].body.session_id,'story-a');
});

for(const scenario of [
  {action:'Start new story',path:'/characters/Alice.png/select',confirm:true,outcome:/New session selected|New story started/i,newStory:true},
  {action:'Delete',path:'/characters/Alice.png',confirm:true,outcome:/Character deleted/i},
  {action:'Restore backup',path:'/character-backups/Alice.png/restore',confirm:true,outcome:/Character backup restored/i,backups:true},
  {action:'Apply',path:'/character-proposals/0123456789abcdef01234567/apply',confirm:true,outcome:/Character changes applied/i,proposal:true},
  {action:'Discard',path:'/character-proposals/0123456789abcdef01234567/discard',outcome:/Preview discarded/i,proposal:true},
  {action:'Upload',path:'/characters',outcome:/Character installed/i},
])test('Characters '+scenario.action+' remains completed after a failed list refresh and cannot be replayed',async t=>{
  const writes=[];let completed=false,allowRefresh=false,active={...session,character_file:'Guide.png'};
  const proposal={nonce:'0123456789abcdef01234567',kind:'optimize',fields:{description:'A refined description.'},original:{description:'Original description.'}};
  const page=await createPage('characters.js',{runJob:async()=>proposal,api:async(path,options={})=>{
    if(options.method&&options.method!=='GET') {
      writes.push({path,...options});completed=true;
      if(scenario.newStory)active={...session,session_id:'story-b',title:'Alice chat'};
      if(path.endsWith('/select'))return {session:active,message:'New session selected. Use /start in Telegram to begin.'};
      if(path.endsWith('/restore'))return {session:active,message:restoreUndoMessage,restored:true,filename:'Alice.png'};
      if(path.endsWith('/apply'))return {message:'Character changes applied.',filename:'Alice.png',rank:'S'};
      if(path.endsWith('/discard'))return {discarded:true};
      if(path==='/characters')return {installed:true,filename:'Alice.png',fields:{name:'Alice'}};
      return {deleted:true};
    }
    if(path.startsWith('/characters?')) {
      if(completed&&!allowRefresh)throw new Error('The character list could not be refreshed.');
      return {session:active,total:1,offset:0,characters:[{filename:'Alice.png',name:'Alice',rank:'S',active:false,digest:'a'.repeat(64)}]};
    }
    if(path==='/characters/Alice.png/portrait')return null;
    if(path==='/character-backups')return {session:active,backups:[{filename:'Alice.png',name:'Alice',installed:true,digest:'a'.repeat(64),backup_digest:'b'.repeat(64),matches_installed:false}]};
    throw new Error('Unexpected API: '+path);
  }});t.after(page.close);
  page.document.getElementById('content').append(await page.pages.characters());
  if(scenario.backups)await press(page.document,'Restore backups');
  if(scenario.proposal) {await press(page.document,'Optimize');await press(page.document,'Generate preview');}
  let action;
  if(scenario.action==='Upload') {
    action=page.document.querySelector('button[aria-label="Add character"]');
    const file=page.document.querySelector('input[type="file"]');
    Object.defineProperty(file,'files',{value:[new page.window.File(['png-fixture'],'Alice.png',{type:'image/png'})]});
    file.dispatchEvent(new page.window.Event('change'));
  } else {
    action=namedButton(page.document,scenario.action);action.click();
    if(scenario.confirm) {await until(()=>page.document.querySelector('dialog[open]'),'character action confirmation');namedButton(page.document,'Confirm').click();}
  }
  await until(()=>action.getAttribute('aria-busy')!=='true','completed character action and failed refresh');
  assert.equal(writes.length,1);assert.equal(writes[0].path,scenario.path);assert.equal(writes[0].body.session_id,'story-a');
  const message=page.document.querySelector('.characters-page [role="alert"]');assert.ok(message,'Completed outcome survives the list refresh error');
  assert.match(message.textContent,scenario.outcome);assert.match(message.textContent,/refresh.*review/i);
  if(scenario.backups) {
    assert.ok(message.textContent.includes(restoreUndoMessage),'The complete returned undo message survives the failed refresh');
    assert.ok(page.document.getElementById('notice').textContent.includes(restoreUndoMessage));
    assert.deepEqual(JSON.parse(JSON.stringify(writes[0].body)),{digest:'a'.repeat(64),backup_digest:'b'.repeat(64),confirm:true,session_id:'story-a'});
  }
  assert.equal(action.disabled,true,'The completed action cannot be repeated from the stale view');
  action.dispatchEvent(new page.window.MouseEvent('click',{bubbles:true}));
  assert.equal(writes.length,1);
  assert.match(page.document.querySelector('.session-context').textContent,scenario.newStory?/Alice chat/:/The Night Archive/);
  allowRefresh=true;await press(page.document,'Refresh and review');
  assert.equal(writes.length,1,'Recovery reads the current state without replaying the mutation');
  assert.equal(namedButton(page.document,'Start new story').disabled,false);
});

test('identical character backups expose no usable restore action while a different backup remains restorable',async t=>{
  const writes=[];
  const page=await createPage('characters.js',{api:async(path,options={})=>{
    if(options.method) {writes.push({path,...options});return {};}
    if(path.startsWith('/characters?'))return {session,total:2,offset:0,characters:[
      {filename:'Alice.png',name:'Alice',rank:'S',active:true,digest:'a'.repeat(64)},
      {filename:'Bob.png',name:'Bob',rank:'A',active:false,digest:'b'.repeat(64)},
    ]};
    if(path.endsWith('/portrait'))return null;
    if(path==='/character-backups')return {session,backups:[
      {filename:'Alice.png',name:'Alice',installed:true,digest:'a'.repeat(64),backup_digest:'a'.repeat(64),matches_installed:true},
      {filename:'Bob.png',name:'Bob',installed:true,digest:'b'.repeat(64),backup_digest:'c'.repeat(64),matches_installed:false},
    ]};
    throw new Error('Unexpected API: '+path);
  }});t.after(page.close);
  page.document.getElementById('content').append(await page.pages.characters());await press(page.document,'Restore backups');
  const backup=name=>[...page.document.querySelectorAll('.card')].find(node=>node.querySelector('h2')?.textContent===name);
  const identical=backup('Alice'),different=backup('Bob');assert.ok(identical);assert.ok(different);
  assert.match(identical.textContent,/already matches this backup/i);
  const identicalActions=[...identical.querySelectorAll('button')].filter(node=>node.textContent==='Restore backup');
  assert.ok(identicalActions.every(control=>control.disabled||control.getAttribute('aria-disabled')==='true'),'An identical backup has no usable Restore action');
  identicalActions.forEach(control=>control.click());assert.equal(writes.length,0);assert.equal(page.document.querySelector('dialog[open]'),null);
  assert.equal(namedButton(different,'Restore backup').disabled,false,'A different backup can still be restored');
});

test('a successful character restore shows the complete returned undo message after refreshing the catalog',async t=>{
  const writes=[],state={session};let catalogReads=0;
  const page=await createPage('characters.js',{state,api:async(path,options={})=>{
    if(path==='/character-backups/Alice.png/restore'&&options.method==='POST') {
      writes.push(options);return {session,message:restoreUndoMessage,restored:true,filename:'Alice.png'};
    }
    if(path.startsWith('/characters?')) {
      catalogReads++;return {session,total:1,offset:0,characters:[{filename:'Alice.png',name:'Alice',rank:'S',active:true,digest:'a'.repeat(64)}]};
    }
    if(path.endsWith('/portrait'))return null;
    if(path==='/character-backups')return {session,backups:[{filename:'Alice.png',name:'Alice',installed:true,digest:'a'.repeat(64),backup_digest:'b'.repeat(64),matches_installed:false}]};
    throw new Error('Unexpected API: '+path);
  }});t.after(page.close);
  page.document.getElementById('content').append(await page.pages.characters());await press(page.document,'Restore backups');
  const restore=namedButton(page.document,'Restore backup');restore.click();await until(()=>page.document.querySelector('dialog[open]'),'restore confirmation');
  assert.equal(writes.length,0,'Restore waits for confirmation');state.session={...session,session_id:'another-view',title:'Another story'};namedButton(page.document,'Confirm').click();
  await until(()=>restore.getAttribute('aria-busy')!=='true','restore and catalog refresh finished');
  assert.equal(catalogReads,2);assert.ok(page.document.querySelector('.character-page-header'));
  assert.equal(writes.length,1);assert.deepEqual(JSON.parse(JSON.stringify(writes[0].body)),{digest:'a'.repeat(64),backup_digest:'b'.repeat(64),confirm:true,session_id:'story-a'});
  const notice=page.document.getElementById('notice');assert.equal(notice.hidden,false);assert.equal(notice.textContent,restoreUndoMessage,'The exact server undo message remains visible after refresh');
});

test('a character confirmation cannot retarget a story returned by a pending search',async t=>{
  let finishSearch,pending=false,active=session;const writes=[];
  const page=await createPage('characters.js',{api:async(path,options={})=>{
    if(options.method) {writes.push({path,...options});return {session:active,message:'New story started.'};}
    if(path.startsWith('/characters?')) {
      const data={session:active,total:1,offset:0,characters:[{filename:'Alice.png',name:'Alice',rank:'S',active:true,digest:'card-digest'}]};
      return pending?new Promise(resolve=>{finishSearch=()=>{pending=false;active={...session,session_id:'story-b',title:'The Sun Archive'};resolve({...data,session:active});};}):data;
    }
    if(path==='/characters/Alice.png/portrait')return null;
    throw new Error('Unexpected API: '+path);
  }});t.after(page.close);
  page.document.getElementById('content').append(await page.pages.characters());pending=true;
  page.document.querySelector('button[aria-label="Search characters"]').click();await until(()=>finishSearch,'character search in flight');
  const start=namedButton(page.document,'Start new story');start.click();await until(()=>page.document.querySelector('dialog[open]'),'old character confirmation');
  finishSearch();await until(()=>page.document.querySelector('.session-context')?.textContent.includes('The Sun Archive'),'new story character results');
  namedButton(page.document,'Confirm').click();await until(()=>start.getAttribute('aria-busy')!=='true','old character confirmation settled');
  assert.equal(writes.length,0,'The retired character view cannot start a story using a new session');
});

test('opening the optimizer retires an older pending character search',async t=>{
  let finishSearch,pending=false;
  const page=await createPage('characters.js',{api:async path=>{
    if(path.startsWith('/characters?')) {
      const data={session,total:1,offset:0,characters:[{filename:'Alice.png',name:'Alice',rank:'S',active:true,digest:'card-digest'}]};
      return pending?new Promise(resolve=>{finishSearch=()=>resolve({...data,session:{...session,session_id:'story-b',title:'The Sun Archive'}});}):data;
    }
    if(path==='/characters/Alice.png/portrait')return null;
    throw new Error('Unexpected API: '+path);
  }});t.after(page.close);
  page.document.getElementById('content').append(await page.pages.characters());pending=true;
  const search=page.document.querySelector('button[aria-label="Search characters"]');search.click();await until(()=>finishSearch,'character search in flight');
  await press(page.document,'Optimize');labelled(page.document,'Manual suggestion').value='Keep this draft.';
  finishSearch();await until(()=>search.getAttribute('aria-busy')!=='true','old search settled');
  assert.equal(labelled(page.document,'Manual suggestion').value,'Keep this draft.');
  assert.match(page.document.querySelector('.session-context').textContent,/The Night Archive/);
});

async function renderOptimizer({runJob,state={session}}) {
  const page=await createPage('characters.js',{state,runJob,api:async path=>{
    if(path.startsWith('/characters?'))return {session,total:1,offset:0,characters:[{filename:'Alice.png',name:'Alice',rank:'S',active:true,digest:'card-digest'}]};
    if(path==='/characters/Alice.png/portrait')return null;
    throw new Error('Unexpected API: '+path);
  }});
  page.document.getElementById('content').append(await page.pages.characters());
  await press(page.document,'Optimize');return page;
}

test('an optimizer failure preserves the suggestion and original story while reporting the failure',async t=>{
  const requests=[],state={session};
  const page=await renderOptimizer({state,runJob:async(path,body,target)=>{
    requests.push({path,body});target.replaceChildren(target.ownerDocument.createTextNode('Working…'));
    throw new Error('The provider could not generate a preview.');
  }});t.after(page.close);
  labelled(page.document,'Manual suggestion').value='Keep the detective’s motivation.';
  state.session={...session,session_id:'other-story'};
  await press(page.document,'Generate preview');
  assert.equal(requests.length,1);assert.equal(requests[0].path,'/characters/Alice.png/optimize');assert.equal(requests[0].body.session_id,'story-a');
  assert.equal(requests[0].body.suggestion,'Keep the detective’s motivation.');assert.equal(requests[0].body.digest,'card-digest');
  assert.equal(labelled(page.document,'Manual suggestion').value,'Keep the detective’s motivation.');
  assert.match(page.document.querySelector('.session-context').textContent,/The Night Archive/);
  assert.match(page.document.querySelector('[role="alert"]').textContent,/provider could not generate/);
});

test('an uncertain optimization keeps the suggestion but locks repeat submission and opens Operations',async t=>{
  let requests=0;
  const page=await renderOptimizer({runJob:async(path,body,target)=>{
    requests++;target.replaceChildren(target.ownerDocument.createTextNode('Working…'));
    const error=new Error('The preview may still be running.');error.uncertain=true;error.jobId='job-one';throw error;
  }});t.after(page.close);
  labelled(page.document,'Manual suggestion').value='Preserve the setting.';
  const generate=namedButton(page.document,'Generate preview');generate.click();
  await until(()=>generate.getAttribute('aria-busy')!=='true','uncertain preview reported');
  assert.equal(labelled(page.document,'Manual suggestion').value,'Preserve the setting.');
  assert.equal(generate.getAttribute('aria-disabled'),'true');
  generate.click();assert.equal(requests,1);
  await press(page.document,'Open operations');assert.deepEqual(page.navigations,['system']);
});
