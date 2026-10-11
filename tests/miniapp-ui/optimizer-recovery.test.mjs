import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

const session={session_id:'story-a',title:'Synthetic story',character_file:'Alice.png'};
const catalog={session,total:1,characters:[{name:'Alice',filename:'Alice.png',digest:'a'.repeat(64)}]};
async function eventually(predicate,label) {
  for(let i=0;i<80;i++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,50));}
  assert.fail('Timed out: '+label);
}
const find=(page,label)=>[...page.document.querySelectorAll('button')].find(n=>n.textContent===label);
async function fixture(runJob) {
  const state={session:null};
  const page=await createPage('characters.js',{runJob,state,api:async path=>{
    if(path.startsWith('/characters?'))return catalog;
    if(path.endsWith('/portrait'))return new Blob(['synthetic']);
    throw new Error('Unexpected API '+path);
  }});
  const owner=new page.window.AbortController();
  page.document.getElementById('content').append(await page.pages.characters({signal:owner.signal}));
  find(page,'Optimize').click();await until(()=>find(page,'Generate preview'));
  return {...page,owner,state};
}

test('optimizer keeps the shared failure panel and its Operations action',async()=>{
  // The real job UI owns this target; a page must not erase its failure/recovery controls.
  const page=await fixture(async(path,body,target)=>{
    target.append(page.document.createTextNode('Operation failed: synthetic validation error'));
    target.append(page.document.createElement('button'));target.lastChild.textContent='Open Operations';
    throw Object.assign(new Error('Synthetic validation error'),{status:422});
  });
  try {
    const guidance=page.document.querySelector('textarea');guidance.value='Keep the synthetic setting and clarify motivation.';
    find(page,'Generate preview').click();await until(()=>!find(page,'Generate preview').disabled);
    assert.equal(guidance.value,'Keep the synthetic setting and clarify motivation.');
    assert.match(page.document.querySelector('.character-operation').textContent,/Operation failed/);
    assert.ok(find(page,'Open Operations'));
  } finally {page.close();}
});

test('aborted character views reject stale references and repeat dispatched clicks',async()=>{
  let calls=0;
  const page=await fixture(async()=>{calls++;return {};});
  try {
    const generate=find(page,'Generate preview');page.owner.abort();
    generate.dispatchEvent(new page.window.Event('click'));generate.dispatchEvent(new page.window.Event('click'));
    await until(()=>!generate.hasAttribute('aria-busy'));
    assert.equal(calls,0,'A retired navigation scope must not submit an optimizer job');
  } finally {page.close();}
});

test('late optimizer success cannot restore a retired view or change current session',async()=>{
  let finish,calls=0;
  const page=await fixture(async()=>{calls++;return new Promise(resolve=>{finish=resolve;});});
  try {
    const generate=find(page,'Generate preview');generate.click();generate.dispatchEvent(new page.window.Event('click'));
    await until(()=>finish);assert.equal(calls,1);
    page.owner.abort();page.document.getElementById('content').replaceChildren();
    page.state.session={session_id:'story-b'};
    finish({kind:'optimize',nonce:'synthetic',fields:{description:'Synthetic revision'}});
    await until(()=>!generate.hasAttribute('aria-busy'));
    assert.equal(page.document.querySelector('#content').textContent,'');
    assert.equal(page.document.querySelector('dialog'),null);
    assert.equal(page.state.session.session_id,'story-b','Late completion must not overwrite the current session');
  } finally {page.close();}
});

for(const failure of ['terminal','rejected','uncertain'])test(`real job UI retains ${failure} failure navigation through nested character actions`,async()=>{
  let posts=0,releaseJobs,operationId;
  const recovered=[];
  const status={session,deployment:{version:'fixture',commit:''},telegram:{state:'observed'},database:{state:'ok',sessions:1,messages:0},uptime_seconds:60,installed_version:'fixture'};
  const page=await createPage('app.js',{telegram:{initData:'isolated-auth-fixture',initDataUnsafe:{start_param:'characters'},ready(){},expand(){}},api:async(path,options={})=>{
    if(path==='/me')return {user:{id:123,name:'Fixture'}};
    if(path.startsWith('/characters?'))return catalog;
    if(path.endsWith('/portrait'))throw Object.assign(new Error('Synthetic portrait unavailable'),{status:404});
    if(path.endsWith('/optimize')){
      posts++;
      if(failure==='rejected')throw Object.assign(new Error('Synthetic stale revision'),{status:409});
      if(failure==='uncertain'){
        operationId=options.body.operation_id;
        throw new Error('Synthetic connection lost after submission');
      }
      return {id:'synthetic-job',state:'queued'};
    }
    if(path.startsWith('/jobs/by-operation/')){
      recovered.push({path,method:options.method});
      return {id:'synthetic-job',state:'succeeded',result:{kind:'optimize'}};
    }
    if(path==='/jobs/synthetic-job')return {id:'synthetic-job',state:'failed',error:'Optimizer could not produce a valid character preview. Review the Utility model and try again.'};
    if(path==='/status')return status;
    if(path==='/jobs')return new Promise(resolve=>{releaseJobs=()=>resolve({jobs:[]});});
    if(path==='/memory-diagnostics')return {reports:[]};
    throw new Error('Unexpected API '+path);
  }});
  try {
    await until(()=>find(page,'Optimize'));
    const menu=page.document.querySelector('.character-actions-menu');menu.open=true;
    find(page,'Optimize').click();await until(()=>find(page,'Generate preview'));
    const guidance=page.document.querySelector('textarea');guidance.value='Keep the synthetic setting and clarify motivation.';
    const generate=find(page,'Generate preview');generate.click();generate.dispatchEvent(new page.window.Event('click'));
    await eventually(()=>find(page,'Open Operations'),'shared Operations failure control');
    await until(()=>!generate.hasAttribute('aria-busy'));assert.equal(posts,1);
    assert.equal(guidance.value,'Keep the synthetic setting and clarify motivation.');
    if(failure==='terminal'){
      assert.match(page.document.querySelector('.character-operation').textContent,/could not produce a valid character preview/);
      assert.doesNotMatch(page.document.getElementById('content').textContent,/resource is unavailable or changed|Status unavailable|Reconnecting/);
      assert.equal(page.document.querySelector('.operation-recovery'),null);
    }
    if(failure==='uncertain'){
      assert.match(page.document.querySelector('.character-operation').textContent,/Status unavailable/);
      assert.equal(generate.getAttribute('aria-disabled'),'true');
      generate.click();generate.dispatchEvent(new page.window.Event('click'));
      await until(()=>!generate.hasAttribute('aria-busy'));
      assert.equal(posts,1,'An unconfirmed preview must block repeat dispatch');
      assert.ok(operationId,'Submission must have an operation ID to recover');
      const recovery=page.document.querySelector('.operation-recovery');
      assert.ok(recovery,'The shared job UI must retain recovery controls');
      find(page,'Recover original operation').click();
      await until(()=>recovery.classList.contains('recovery-completed'),'original operation recovered');
      assert.deepEqual(recovered,[{path:'/jobs/by-operation/'+operationId,method:'GET'}]);
      assert.equal(posts,1,'Recovering the original operation must not submit another preview');
      assert.equal(guidance.value,'Keep the synthetic setting and clarify motivation.');
      assert.equal(generate.getAttribute('aria-disabled'),'true');
    }
    const operations=find(page,'Open Operations');operations.click();operations.dispatchEvent(new page.window.Event('click'));
    await until(()=>releaseJobs);assert.equal(page.document.body.dataset.page,'system');
    assert.equal(page.document.getElementById('content').inert,true);
    releaseJobs();await until(()=>!page.document.getElementById('content').inert);
    assert.match(page.document.getElementById('content').textContent,/Operations/);
    assert.equal(posts,1,'Recovery navigation must not resubmit optimization');
  } finally {page.close();}
});

test('superseding delayed Operations navigation releases inert content and ignores its late response',async()=>{
  let release;
  const page=await createPage('app.js',{telegram:{initData:'isolated-auth-fixture',initDataUnsafe:{start_param:'characters'},ready(){},expand(){}},api:async path=>{
    if(path==='/me')return {user:{id:123,name:'Fixture'}};
    if(path.startsWith('/characters?'))return catalog;
    if(path.endsWith('/portrait'))throw Object.assign(new Error('No portrait'),{status:404});
    if(path==='/status')return new Promise(resolve=>{release=resolve;});
    if(path==='/jobs')return {jobs:[]};
    if(path==='/memory-diagnostics')return {reports:[]};
    throw new Error('Unexpected API '+path);
  }});
  try {
    await until(()=>find(page,'Optimize'));
    const pending=page.namespace.navigate('system');await until(()=>release);
    await page.namespace.navigate('characters');assert.equal(page.document.getElementById('content').inert,false);
    release({session,deployment:{version:'fixture',commit:''},telegram:{state:'observed'},database:{state:'ok',sessions:1,messages:0}});
    await pending;assert.equal(page.document.body.dataset.page,'characters');
    assert.ok(find(page,'Optimize'));assert.equal(page.document.getElementById('content').inert,false);
  } finally {page.close();}
});
