import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

const session={session_id:'story-a'};
const control=(page,label)=>[...page.document.querySelectorAll('button')].find(node=>node.textContent===label);

for (const failure of ['none','file','console','event','unconfigured','unknown']) {
  test('diagnostics explain logging health: '+failure,async()=>{
    const data={available:true,truncated:false,limitations:'Bounded retained-log window.',
      usage:{attempts:1,reported_attempts:1,input_tokens:12,output_tokens:8,complete:false},usage_by_purpose:[],traces:[],
      events:[{timestamp:'2026-10-15T06:00:00.000Z',level:'ERROR',event:'provider.finish',status:'failed',
        model:'<img src=x onerror=alert(1)>',error_type:'TimeoutError',rejection_code:'output_capped'}]};
    if(failure!=='unknown')data.runtime={logging:{scope:'process',
      file_configured:failure!=='unconfigured',console_configured:true,
      file_failures_observed:failure==='file',console_failures_observed:failure==='console',
      event_failures_observed:failure==='event'}};
    const state={session:{...session}};
    const page=await createPage('diagnostics.js',{api:async()=>data,state});
    page.document.getElementById('content').append(page.namespace.diagnosticsCard(session));
    try {
      control(page,'Load diagnostics').click();
      await until(()=>page.document.body.textContent.includes('provider.finish'));
      const text=page.document.body.textContent;
      assert.match(text,/Logging health/);
      assert.match(text,/TimeoutError/);
      assert.match(text,/output_capped/);
      assert.match(page.document.querySelector('li strong').textContent,/^ERROR/);
      if(failure==='none')assert.match(text,/No logging failures observed/);
      else if(failure==='unknown')assert.match(text,/Logging health unavailable/);
      else assert.match(text,/Logging is degraded/);
      if(['file','event','unconfigured'].includes(failure))assert.match(text,/events may be missing/);
      if(failure==='console')assert.match(text,/Journal writes have failed/);
      assert.equal(page.document.querySelector('img'),null);
    } finally {page.close();}
  });
}
