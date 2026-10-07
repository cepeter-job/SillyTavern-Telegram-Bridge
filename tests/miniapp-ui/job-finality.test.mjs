import assert from 'node:assert/strict';
import test from 'node:test';
import {createJobController} from '../../bridge/miniapp_assets/jobs.js';

for (const status of [401,403,404]) test(`Unconfirmed POST followed by ${status} remains unresolved, not a new action`,async()=>{
  let posts=0;
  const controller=createJobController({identity:()=>123,request:async(path,options={})=>{
    if(options.method==='POST'){posts++;throw new TypeError('accepted response lost');}
    throw Object.assign(new Error('lookup unavailable'),{status});
  },retryDelays:[]});
  let id;
  await assert.rejects(controller.run('/op',{session_id:'a'}),error=>{id=error.operationId;return error.uncertain;});
  await assert.rejects(controller.resume(id),error=>error.status===status&&error.uncertain&&!error.recoverable);
  await assert.rejects(controller.run('/op',{session_id:'a'}));
  assert.equal(posts,1,'An unavailable lookup cannot authorize a replacement POST');
});

test('An expired unconfirmed action cannot silently become a fresh submission',async()=>{
  let time=1000,posts=0;
  const controller=createJobController({identity:()=>123,now:()=>time,horizon:100,request:async()=>{
    posts++;throw new TypeError('accepted response lost');
  }});
  await assert.rejects(controller.run('/op',{session_id:'a'}));
  time+=101;
  for(let n=0;n<2;n++)await assert.rejects(controller.run('/op',{session_id:'a'}),error=>error.status===410&&error.uncertain);
  assert.equal(posts,1);
});

test('A confirmed submission rejection permits a later intentional action',async()=>{
  let posts=0;
  const controller=createJobController({identity:()=>123,request:async()=>{
    if(++posts===1)throw Object.assign(new Error('validation rejected'),{status:400});
    return {id:'new-job',state:'succeeded',result:7};
  }});
  await assert.rejects(controller.run('/op',{}),error=>!error.uncertain);
  assert.equal(await controller.run('/op',{}),7);
  assert.equal(posts,2);
});
