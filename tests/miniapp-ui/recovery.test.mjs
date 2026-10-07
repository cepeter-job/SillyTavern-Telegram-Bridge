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
