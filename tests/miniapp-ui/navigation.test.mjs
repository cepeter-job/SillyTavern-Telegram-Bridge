import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

async function appFixture(extraApi) {
  let back=()=>{},closed=0;
  const telegram={initData:'isolated-auth-fixture',initDataUnsafe:{start_param:'tools'},ready(){},expand(){},close(){closed++;},BackButton:{onClick(fn){back=fn;},hide(){},show(){}}};
  const session={session_id:'story-a',title:'The lighthouse',character_file:'Alice.png',mode:'normal'};
  const page=await createPage('app.js',{telegram,api:async(path,options)=>{
    if(path==='/me')return {user:{name:'Fixture'}};
    if(path==='/status')return {session};
    if(extraApi)return extraApi(path,options);
    throw Object.assign(new Error('Read fixture unavailable'),{status:503});
  }});
  await until(()=>page.document.querySelector('#navigation button'));
  return {...page,back:()=>back(),closed:()=>closed};
}

test('Four-tab navigation preserves legacy destinations and explicit parent/back paths',async()=>{
  const page=await appFixture();
  try {
    assert.deepEqual([...page.document.querySelectorAll('#navigation button')].map(n=>n.textContent.trim()),['Home','Characters','Tools','Settings']);
    for(const [key,group,parent] of [['director','dashboard','dashboard'],['trackers','dashboard','dashboard'],['sessions','dashboard','dashboard'],['models','settings','settings'],['system','settings','settings'],['memory','tools','tools'],['manage','tools','tools'],['advanced','settings','settings']]) {
      await page.namespace.navigate(key);
      assert.equal(page.document.querySelector('#navigation [aria-current="page"]').dataset.page,group,key+' group');
      page.back();await until(()=>page.document.body.dataset.page===parent,key+' parent');
    }
    await page.namespace.navigate('tools');
    const tools=[...page.document.querySelectorAll('.manage-link')].map(n=>n.dataset.page);
    for(const key of ['director','trackers','check','imagine','personas','worlds','memory','npcs','databank'])assert.ok(tools.includes(key),key+' remains reachable');
    await page.namespace.navigate('settings');
    const settings=[...page.document.querySelectorAll('.manage-link')].map(n=>n.dataset.page);
    for(const key of ['models','generation','system','usage'])assert.ok(settings.includes(key),key+' remains reachable');
  } finally {page.close();}
});

test('Check and Imagine explain the Telegram handoff without sending a command',async()=>{
  const page=await appFixture();
  try {
    for(const key of ['check','imagine']) {
      await page.namespace.navigate(key);
      const view=page.document.getElementById('content');
      assert.match(view.textContent,new RegExp('/'+key));assert.match(view.textContent,/Telegram/);
      assert.match(view.textContent,/The lighthouse/,'Handoff retains actual current session identity');
      assert.match(view.textContent,/send|enter|type/i,'Handoff explains the required command step');
      const open=[...view.querySelectorAll('button')].find(n=>n.textContent.trim()==='Open chat');assert.ok(open);open.click();
    }
    assert.equal(page.closed(),2,'The native action only closes back to chat');
  } finally {page.close();}
});

test('An interrupted operation keeps its identity and routes recovery to Operations',async()=>{
  let submissions=0;
  const page=await appFixture(async(path,{method})=>{
    if(path==='/director/reassess'&&method==='POST'){submissions++;return {id:'known-job',state:'queued'};}
    if(path==='/jobs/known-job')throw new Error('Network interrupted');
    throw new Error('Unexpected request '+path);
  });
  try {
    const target=page.document.createElement('section');page.document.body.append(target);
    await assert.rejects(page.namespace.runJob('/director/reassess',{session_id:'story-a'},target),error=>error.uncertain===true&&error.jobId==='known-job');
    assert.equal(submissions,1);assert.match(target.textContent,/Unable to confirm|Status unavailable/);
    const operations=[...target.querySelectorAll('button')].find(n=>/operations/i.test(n.textContent));assert.ok(operations);operations.click();
    await until(()=>page.document.body.dataset.page==='system');
  } finally {page.close();}
});

test('A server error after submission is uncertain because the job may have been accepted',async()=>{
  const page=await appFixture(async()=>{throw Object.assign(new Error('The final response is unavailable'),{status:500});});
  try {
    const target=page.document.createElement('section');page.document.body.append(target);
    await assert.rejects(page.namespace.runJob('/director/reassess',{session_id:'story-a'},target),error=>error.uncertain===true&&Boolean(error.operationId));
    assert.match(target.textContent,/Status unavailable/);assert.match(target.textContent,/Operations/);
  } finally {page.close();}
});
