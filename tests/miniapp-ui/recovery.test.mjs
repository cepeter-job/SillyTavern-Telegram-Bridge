import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

async function fixture(handler) {
  const page=await createPage('app.js',{telegram:{initData:'isolated-auth-fixture',initDataUnsafe:{start_param:'tools'},ready(){},expand(){}},api:async(path,options)=>{
    if(path==='/me')return {user:{id:123,name:'Fixture'}};
    if(path==='/status')return {session:{session_id:'story-a'}};
    return handler(path,options);
  }});
  await until(()=>page.document.querySelector('#navigation button'));
  return page;
}

test('An explicit operation ID is preserved instead of silently replaced',async()=>{
  const submitted=[];
  const page=await fixture(async(path,{body})=>{submitted.push(body);return {id:'original-job',state:'succeeded',result:{ok:true}};});
  try {
    await page.namespace.runJob('/director/reassess',{session_id:'story-a',operation_id:'original-operation'});
    assert.equal(submitted[0].operation_id,'original-operation');
  } finally {page.close();}
});

test('Recovery remains reachable after a page replaces the operation target',async()=>{
  let posts=0,lookup=0;
  const page=await fixture(async(path,options={})=>{
    if(options.method==='POST'){posts++;throw new Error('response lost');}
    if(path.startsWith('/jobs/by-operation/')){lookup++;return {id:'saved-job',state:'succeeded',result:{value:42}};}
    throw new Error('Unexpected request '+path);
  });
  try {
    const target=page.document.createElement('section');page.document.body.append(target);
    await assert.rejects(page.namespace.runJob('/director/reassess',{session_id:'story-a'},target));
    target.replaceChildren(page.document.createTextNode('Draft retained'));
    const resume=[...page.document.querySelectorAll('button')].find(n=>n.textContent==='Recover original operation');
    assert.ok(resume,'Recovery lives outside the page-owned target');resume.click();
    await until(()=>page.document.querySelector('.recovery-completed'));
    assert.equal(posts,1);assert.equal(lookup,1);assert.match(target.textContent,/Draft retained/);
  } finally {page.close();}
});

for(const status of [401,403,404])test(`A null error envelope preserves permanent HTTP ${status} and stops recovery`,async()=>{
  let posts=0,reads=0;
  const page=await fixture(async(path,options={})=>{
    assert.equal(options.method,'POST');posts++;
    return {id:'accepted-job',state:'running'};
  });
  try {
    const fetch=page.window.fetch;
    page.window.fetch=async(path,options)=>{
      if(path.startsWith('/api/v1/jobs/')){
        reads++;
        return new Response('null',{status,headers:{'Content-Type':'application/json'}});
      }
      return fetch(path,options);
    };
    await assert.rejects(page.namespace.runJob('/director/reassess',{session_id:'story-a'}),error=>{
      assert.equal(error.status,status);
      assert.equal(error.recoverable,false);
      assert.equal(error.uncertain,true);
      return true;
    });
    assert.equal(posts,1);assert.equal(reads,1);
    assert.equal(page.document.querySelector('.operation-recovery'),null);
  } finally {page.close();}
});
