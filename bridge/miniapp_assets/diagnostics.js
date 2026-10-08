import {api,state} from './app.js';
import {el,card,button,empty,field} from './ui.js';

function count(value) {return Number.isSafeInteger(value)&&value>=0?value.toLocaleString():'Unknown';}
function usageView(data) {
  const usage=data.usage||{};
  const view=el('section',{},el('h3',{},'Reported model usage'),
    el('p',{},usage.complete?'Usage reported for every displayed attempt.':'Partial / unknown usage; missing values are not zero.'),
    el('p',{},'Attempts '+count(usage.attempts)+' · Reported '+count(usage.reported_attempts)),
    el('p',{},'Input '+count(usage.input_tokens)+' · Output '+count(usage.output_tokens)));
  for(const item of (data.usage_by_purpose||[]).slice(0,32)) {
    view.append(el('p',{class:'muted'},String(item.purpose||'Unscoped')+': '+count(item.input_tokens)+' input · '+count(item.output_tokens)+' output · '+count(item.attempts)+' attempts'));
  }
  return view;
}
function eventView(item) {
  const heading=[item.event,item.status].filter(Boolean).join(' · ');
  const view=el('li',{},el('strong',{},heading),el('p',{class:'muted'},String(item.timestamp||'')));
  const keys=['request_id','job_id','worker_id','call_id','attempt','purpose','provider','model','reason','phase',
    'previous_model','next_model','http_status','elapsed_ms','queue_ms','source_message_id','revision'];
  const values=keys.filter(key=>item[key]!==undefined).map(key=>key.replaceAll('_',' ')+': '+String(item[key]));
  if(values.length)view.append(el('p',{},values.join(' · ')));
  return view;
}

export function diagnosticsCard(session) {
  const sessionId=String(session?.session_id||'');
  const request=el('input',{type:'text',maxlength:160,autocomplete:'off','data-diagnostic-filter':'request_id'});
  const purpose=el('input',{type:'text',maxlength:160,autocomplete:'off','data-diagnostic-filter':'purpose'});
  const level=el('select',{'data-diagnostic-filter':'level'},
    ...['DEBUG','INFO','WARNING','ERROR','CRITICAL'].map(value=>el('option',{value},value==='DEBUG'?'All levels':value)));
  const output=el('div',{'aria-live':'polite'},empty('Load recent events for this story. No prompts, replies or private plans are included.'));
  const view=card('Troubleshooting diagnostics',
    el('p',{class:'muted'},'Private to your active story. This is a bounded retained-log window, not a complete audit trail.'),
    el('div',{class:'grid'},field('Request ID',request),field('Model-call purpose',purpose),field('Minimum severity',level)));
  let generation=0;
  function assertCurrent() {
    if(!sessionId||String(state.session?.session_id||'')!==sessionId) {
      throw Object.assign(new Error('The active session changed. Refresh System before loading diagnostics.'),{status:409});
    }
  }
  function query() {
    assertCurrent();
    const values=new URLSearchParams({session_id:sessionId,limit:'200',level:level.value});
    if(request.value.trim())values.set('request_id',request.value.trim());
    if(purpose.value.trim())values.set('purpose',purpose.value.trim());
    return '?'+values.toString();
  }
  async function load() {
    const filters=query(),sequence=++generation;
    output.replaceChildren(el('p',{},'Reading the retained diagnostic window…'));
    try {
      const data=await api('/diagnostics'+filters);
      assertCurrent();
      if(sequence!==generation)return;
      const status=el('p',{class:'info-note'},data.available
        ?(data.truncated?'Partial window: read or event limit reached.':'Available retained window; earlier or filtered events may be absent.')
        :'Diagnostic log unavailable. Check the running version and configured logging.');
      const traces=el('section',{},el('h3',{},'Related requests'));
      for(const trace of (data.traces||[]).slice(0,100)) {
        const summary=String(trace.request_id||'Unlinked request')+' · '+count(trace.events)+' events · '+count(trace.failures)+' failure signals · '+count(trace.fallbacks)+' fallbacks';
        const row=el('div',{},el('p',{},summary));
        if(trace.request_id)row.append(button('Trace '+trace.request_id,async()=>{
          request.value=trace.request_id;purpose.value='';level.value='DEBUG';await load();
        },'secondary'));
        traces.append(row);
      }
      const events=Array.isArray(data.events)?data.events.slice(0,500):[];
      const timeline=el('ol',{'aria-label':'Diagnostic event timeline'},...events.map(eventView));
      output.replaceChildren(status,el('p',{class:'muted'},String(data.limitations||'')),usageView(data),traces,
        events.length?timeline:empty('No matching events in the retained window. This does not prove that no work occurred.'));
    } catch(error) {
      if(sequence===generation)output.replaceChildren(el('p',{role:'alert'},error.message||'Diagnostics could not be loaded.'));
      throw error;
    }
  }
  async function exportDiagnostics() {
    const data=await api('/diagnostics/export'+query());
    assertCurrent();
    const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    const download=el('a',{href:url,download:'sillytavern-diagnostics.json',hidden:true});
    try {document.body.append(download);download.click();}
    finally {download.remove();URL.revokeObjectURL(url);}
  }
  view.append(el('div',{class:'actions'},button('Load diagnostics',load,'secondary'),
    button('Export diagnostics',exportDiagnostics,'secondary')),output);
  return view;
}
