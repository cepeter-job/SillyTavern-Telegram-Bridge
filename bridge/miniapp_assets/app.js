import {el,card,button,notice} from './ui.js';
const telegram = window.Telegram?.WebApp;
const raw = telegram?.initData || '';
export const state = {user:null, session:null};
export async function api(path, {method='GET', body, signal, binary=false}={}) {
  if (!raw) throw new Error('Open this page using the Bridge menu inside Telegram.');
  const response = await fetch('/api/v1' + path, {method, cache:'no-store', credentials:'omit',
    headers:{Authorization:'tma '+raw, ...(body === undefined ? {} : {'Content-Type':'application/json'})},
    body:body === undefined ? undefined : JSON.stringify(body), signal:signal || AbortSignal.timeout(30000)});
  if (binary && response.ok) return response.blob();
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error?.message || 'Request failed.'); error.status=response.status; throw error; }
  return result;
}
export function sessionBody(extra={}) { if (!state.session) throw new Error('Open or create a session first.'); return {session_id:state.session.session_id,...extra}; }
const pages = {dashboard:{label:'Dashboard',render:async()=>card('Your workspace',el('p',{},'Manage your characters, models and context here. Continue the conversation in your Telegram chat.'),el('p',{class:'muted'},'Signed in as '+state.user.name))}};
export function registerPage(key, label, render) { pages[key] = {label,render}; }
let current='dashboard', generation=0;
export async function navigate(key=current) {
  if (!pages[key]) key='dashboard'; current=key; const seq=++generation;
  document.getElementById('navigation').replaceChildren(...Object.entries(pages).map(([name,page])=>{
    const b=button(page.label,()=>navigate(name)); if(name===current)b.setAttribute('aria-current','page'); return b;
  }));
  document.getElementById('content').setAttribute('aria-busy','true');
  try { const view=await pages[key].render(); if(seq===generation)document.getElementById('content').replaceChildren(view); }
  catch(error){if(seq===generation)document.getElementById('content').replaceChildren(card('Could not load this page',el('p',{},error.message),button('Retry',()=>navigate(key))));}
  finally{document.getElementById('content').removeAttribute('aria-busy');}
  try { if(key==='dashboard')telegram?.BackButton?.hide();else telegram?.BackButton?.show(); } catch {}
}
async function start() {
  try { telegram?.ready(); telegram?.expand(); telegram?.BackButton?.onClick(()=>navigate('dashboard')); } catch {}
  document.getElementById('refresh').addEventListener('click',()=>navigate());
  try {
    const data=await api('/me'); state.user=data.user;
    document.getElementById('identity').textContent=data.user.name+' · Private bot chat';
    await import('./characters.js');
    await import('./models.js');
    await navigate(telegram?.initDataUnsafe?.start_param || 'dashboard');
  } catch(error) {
    document.getElementById('identity').textContent='Authentication required';
    document.getElementById('content').replaceChildren(card('Open from Telegram',el('p',{},error.message)));
    notice(error.message);
  }
}
start();

export async function runJob(path, body, target) {
  const pending = await api(path,{method:'POST',body:{...body,operation_id:crypto.randomUUID()}});
  let job=pending;
  const progress=card('Operation',el('p',{},'Working through the bridge’s bounded queue…'),el('progress',{}));
  if(target)target.replaceChildren(progress);
  const deadline=Date.now()+600000;
  while(['queued','running'].includes(job.state)) {
    if(Date.now()>deadline)throw new Error('Operation is still running. Its result is available in System → Operations.');
    await new Promise(resolve=>setTimeout(resolve,1500));
    if(document.hidden)continue;
    job=await api('/jobs/'+encodeURIComponent(job.id));
    progress.querySelector('p').textContent=job.state==='queued'?'Queued; waiting for a worker.':'Running. You may leave this page; the operation continues on the bridge.';
  }
  if(job.state!=='succeeded')throw new Error(job.error||'Operation did not complete.');
  return job.result;
}
