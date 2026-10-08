import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

const session={session_id:'story-a'};
const sample=()=>({available:true,truncated:true,bytes_scanned:128,limitations:'Bounded retained-log window.',
  usage:{attempts:2,reported_attempts:1,input_tokens:12,output_tokens:null,complete:false},usage_by_purpose:[],
  traces:[{request_id:'tg-42',events:3,failures:1,fallbacks:1,last_event:'provider.finish',last_status:'succeeded'}],
  events:[{timestamp:'2026-10-08T06:00:00.000Z',event:'provider.finish',request_id:'tg-42',purpose:'story',status:'failed',model:'<img src=x onerror=alert(1)>',reason:'timeout'}]});
const control=(page,label)=>[...page.document.querySelectorAll('button')].find(node=>node.textContent===label);

async function fixture(api) {
  const state={session:{...session}};
  const page=await createPage('diagnostics.js',{api,state});
  page.document.getElementById('content').append(page.namespace.diagnosticsCard(session));
  return {...page,state};
}

test('diagnostics load only on request, render text safely and mark partial usage',async()=>{
  const calls=[];const page=await fixture(async path=>{calls.push(path);return sample();});
  try {
    assert.equal(calls.length,0);
    control(page,'Load diagnostics').click();
    await until(()=>page.document.body.textContent.includes('provider.finish'));
    assert.match(calls[0],/session_id=story-a/);
    assert.equal(page.document.querySelector('img'),null);
    assert.match(page.document.body.textContent,/Partial/);
    assert.match(page.document.body.textContent,/Unknown/);
    assert.match(page.document.body.textContent,/12/);
    assert.match(page.document.body.textContent,/timeout/);
    assert.match(page.document.body.textContent,/<img src=x/);
  } finally {page.close();}
});

test('filters and trace selection use the captured session',async()=>{
  const calls=[];const page=await fixture(async path=>{calls.push(path);return sample();});
  try {
    page.document.querySelector('[data-diagnostic-filter=request_id]').value='tg-42';
    page.document.querySelector('[data-diagnostic-filter=purpose]').value='summary';
    page.document.querySelector('[data-diagnostic-filter=level]').value='WARNING';
    control(page,'Load diagnostics').click();
    await until(()=>calls.length===1);
    const query=new URL(calls[0],'https://fixture.test').searchParams;
    assert.equal(query.get('request_id'),'tg-42');
    assert.equal(query.get('purpose'),'summary');
    assert.equal(query.get('level'),'WARNING');
    assert.equal(query.get('session_id'),'story-a');
    assert.equal(query.get('limit'),'200');
  } finally {page.close();}
});

test('late responses and exports cannot follow a changed active session',async()=>{
  let resolve;const calls=[];
  const page=await fixture(path=>{calls.push(path);return new Promise(done=>{resolve=done;});});
  try {
    control(page,'Load diagnostics').click();await until(()=>Boolean(resolve));
    page.state.session={session_id:'story-b'};resolve(sample());
    await until(()=>page.document.body.textContent.includes('session changed'));
    assert.equal(page.document.body.textContent.includes('<img src=x'),false);
    control(page,'Export diagnostics').click();
    await new Promise(done=>setTimeout(done,10));
    assert.equal(calls.length,1,'stale view must not request another session');
  } finally {page.close();}
});

test('export downloads only the authorized response and releases the object URL',async()=>{
  const calls=[];const page=await fixture(async path=>{calls.push(path);return {...sample(),schema:1,kind:'sillytavern-diagnostics'};});
  const downloads=[],revoked=[];
  page.window.HTMLAnchorElement.prototype.click=function(){downloads.push({name:this.download,url:this.href});};
  page.window.URL.revokeObjectURL=url=>revoked.push(url);
  try {
    control(page,'Export diagnostics').click();
    await until(()=>downloads.length===1);
    assert.match(calls[0],/^\/diagnostics\/export\?/);
    assert.match(calls[0],/session_id=story-a/);
    assert.equal(downloads[0].name,'sillytavern-diagnostics.json');
    assert.deepEqual(revoked,['blob:isolated-fixture']);
    assert.equal(page.document.querySelector('a[download]'),null);
  } finally {page.close();}
});
