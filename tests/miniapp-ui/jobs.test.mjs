import assert from 'node:assert/strict';
import test from 'node:test';
import {createJobController} from '../../bridge/miniapp_assets/jobs.js';

const succeeded=(id='job-1')=>({id,state:'succeeded',result:{value:42}});
function fixture(request,options={}) {
  const sleeps=[],updates=[];let time=1000,actor='alice';
  const controller=createJobController({request,identity:()=>actor,now:()=>time,
    sleep:async ms=>{sleeps.push(ms);time+=ms;},pollInterval:1,retryDelays:[2,3],horizon:1000,...options});
  return {controller,sleeps,updates,notify:value=>updates.push(value),actor:value=>{actor=value;},advance:ms=>{time+=ms;}};
}

test('An accepted POST with a lost response is recovered by identity without a second POST',async()=>{
  const calls=[];let saved;
  const f=fixture(async(path,options={})=>{
    calls.push([path,options]);
    if(options.method==='POST'){saved=options.body.operation_id;throw new TypeError('response lost');}
    assert.equal(path,'/jobs/by-operation/'+saved);return succeeded();
  });
  let error;try{await f.controller.run('/memory/summary/generate',{session_id:'a'},f.notify);}catch(e){error=e;}
  assert.equal(error.uncertain,true);assert.equal(error.recoverable,true);
  assert.deepEqual(await f.controller.resume(error.operationId,f.notify),{value:42});
  assert.equal(calls.filter(([,o])=>o.method==='POST').length,1);
});

test('The same unresolved action reuses identity while another payload never replaces it',async()=>{
  let posts=0;const lookups=[];
  const f=fixture(async(path,options={})=>{
    if(options.method==='POST'){posts++;throw new Error('lost');}
    lookups.push(path);return succeeded();
  });
  const body={session_id:'a',revision:7,nested:{b:2,a:1},operation_id:'user-action'};
  await assert.rejects(f.controller.run('/op',body),e=>e.operationId==='user-action');
  body.revision=8;
  await assert.rejects(f.controller.run('/op',body),e=>e.status===409);
  await f.controller.run('/op',{session_id:'a',revision:7,nested:{a:1,b:2}});
  assert.equal(posts,1);assert.equal(lookups.length,1);assert.match(lookups[0],/user-action$/);
});

test('Concurrent matching runs and resume paths share one flight',async()=>{
  let dispatch,posts=0,gets=0;
  const gate=new Promise(resolve=>{dispatch=resolve;});
  const f=fixture(async(path,options={})=>{
    if(options.method==='POST'){posts++;await gate;return {id:'job-1',state:'running'};}
    gets++;return succeeded();
  });
  const first=f.controller.run('/op',{session_id:'a',operation_id:'same'});
  const second=f.controller.run('/op',{operation_id:'same',session_id:'a'});
  await new Promise(resolve=>setTimeout(resolve,10));dispatch();
  assert.deepEqual(await first,await second);assert.equal(posts,1);assert.equal(gets,1);
});

test('Intentional new actions after completion get distinct operation IDs',async()=>{
  const ids=[];const f=fixture(async(path,options)=>{ids.push(options.body.operation_id);return succeeded();});
  await f.controller.run('/op',{session_id:'a'});await f.controller.run('/op',{session_id:'a'});
  assert.equal(ids.length,2);assert.notEqual(ids[0],ids[1]);
});

test('Transient polling failures reconnect using only the original job',async()=>{
  let posts=0,gets=0;
  const f=fixture(async(path,options={})=>{
    if(options.method==='POST'){posts++;return {id:'job-1',state:'running'};}
    assert.equal(path,'/jobs/job-1');if(++gets<3)throw Object.assign(new Error('temporary'),{status:503});return succeeded();
  });
  assert.deepEqual(await f.controller.run('/op',{},f.notify),{value:42});
  assert.equal(posts,1);assert.equal(gets,3);assert.ok(f.updates.some(u=>u.state==='reconnecting'));
  assert.deepEqual(f.sleeps,[1,2,3]);
});

test('Exhausted polling can resume the original job, with one shared recovery flight',async()=>{
  let posts=0,gets=0,offline=true;
  const f=fixture(async(path,options={})=>{
    if(options.method==='POST'){posts++;return {id:'job-1',state:'queued'};}
    gets++;if(offline)throw new Error('offline');return succeeded();
  });
  let error;try{await f.controller.run('/op',{});}catch(e){error=e;}
  assert.equal(error.jobId,'job-1');assert.equal(gets,3);offline=false;
  await Promise.all([f.controller.resume(error.operationId),f.controller.resume(error.operationId)]);
  assert.equal(posts,1);assert.equal(gets,4);
});

for(const status of [401,403,404])test('Permanent GET '+status+' never starts a replacement operation',async()=>{
  let calls=0;const f=fixture(async(path,options={})=>{
    calls++;if(options.method==='POST')return {id:'job-1',state:'running'};
    throw Object.assign(new Error('unavailable'),{status});
  });
  let error;try{await f.controller.run('/op',{});}catch(e){error=e;}
  assert.equal(error.recoverable,false);assert.equal(calls,2);
  await assert.rejects(f.controller.resume(error.operationId));assert.equal(calls,2);
});

for(const state of ['failed','interrupted'])test('Terminal '+state+' is not a transport failure',async()=>{
  const f=fixture(async()=>({id:'job-1',state,error:'terminal'}));
  await assert.rejects(f.controller.run('/op',{}),e=>e.uncertain===false&&e.recoverable===false);
});

test('Missing recovery records do not re-submit, even when the first response was lost',async()=>{
  let posts=0;const f=fixture(async(path,options={})=>{
    if(options.method==='POST'){posts++;throw new Error('lost');}
    throw Object.assign(new Error('pruned or not accepted'),{status:404});
  });
  let error;try{await f.controller.run('/op',{});}catch(e){error=e;}
  await assert.rejects(f.controller.resume(error.operationId),e=>e.status===404&&!e.recoverable);
  assert.equal(posts,1);
});

test('Actor changes and expiry block recovery before network access',async()=>{
  let calls=0;const f=fixture(async()=>{calls++;throw new Error('lost');});
  let error;try{await f.controller.run('/op',{});}catch(e){error=e;}
  f.actor('bob');await assert.rejects(f.controller.resume(error.operationId),e=>e.status===403);
  f.actor('alice');f.advance(1001);await assert.rejects(f.controller.resume(error.operationId),e=>e.status===410);
  assert.equal(calls,1);
});

test('Unknown state is not silently treated as success',async()=>{
  const f=fixture(async()=>({id:'job-1',state:'other'}));
  await assert.rejects(f.controller.run('/op',{}),e=>e.uncertain===true&&!e.recoverable);
});

test('Pending recovery records have a hard admission bound',async()=>{
  let calls=0;const f=fixture(async()=>{calls++;throw new Error('lost');},{capacity:2});
  for(const n of [1,2])await assert.rejects(f.controller.run('/op',{n}));
  await assert.rejects(f.controller.run('/op',{n:3}),e=>e.status===429);
  assert.equal(calls,2);
});
