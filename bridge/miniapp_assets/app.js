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
export function createSessionScope() {
  let sessionId='';
  return {
    set(session) {
      sessionId=String(session?.session_id||'');
      state.session=session;
    },
    body(extra={}) {
      if(!sessionId)throw new Error('Open or create a session first.');
      return {...extra,session_id:sessionId};
    }
  };
}
export function sessionBody(extra={}) { if (!state.session) throw new Error('Open or create a session first.'); return {session_id:state.session.session_id,...extra}; }

const pages = {dashboard:{label:'Home',render:async()=>card('Your workspace',el('p',{},'Manage your characters, models and context here. Continue the conversation in your Telegram chat.'),el('p',{class:'muted'},'Signed in as '+state.user.name))}};
const primaryPages = [['dashboard','Home'],['characters','Characters'],['sessions','Sessions'],['memory','Memory']];
const secondaryPages = ['models','personas','worlds','databank','system'];
const pageHints = {
  dashboard:'PRIVATE WORKSPACE',characters:'CHARACTER LIBRARY',sessions:'CONVERSATIONS',memory:'MEMORY & CONTEXT',
  models:'GENERATION',personas:'IDENTITY',worlds:'LORE & CONTEXT',databank:'RETRIEVAL',system:'RUNTIME'
};
export function registerPage(key, label, render) { pages[key] = {label,render}; }
let current='dashboard', generation=0;

function closeMore() {
  const sheet=document.getElementById('more-menu');
  if(!sheet?.hasAttribute('open'))return;
  if(typeof sheet.close==='function')sheet.close();else sheet.removeAttribute('open');
}
function renderMoreNavigation() {
  const target=document.getElementById('more-navigation');
  target.replaceChildren(...secondaryPages.map(key=>{
    const page=pages[key];if(!page)return document.createDocumentFragment();
    const control=button(page.label,async()=>{closeMore();await navigate(key);},'more-link');
    control.setAttribute('data-page',key);
    if(key===current)control.setAttribute('aria-current','page');
    return control;
  }));
}
function openMore() {
  renderMoreNavigation();
  const sheet=document.getElementById('more-menu');
  if(typeof sheet.showModal==='function')sheet.showModal();else sheet.setAttribute('open','');
}
function renderNavigation() {
  const controls=primaryPages.map(([key,label])=>{
    const control=button(label,()=>navigate(key),'nav-button');control.setAttribute('data-page',key);
    if(key===current)control.setAttribute('aria-current','page');return control;
  });
  const more=button('More',openMore,'nav-button');more.setAttribute('data-page','more');
  if(secondaryPages.includes(current))more.setAttribute('aria-current','page');
  controls.push(more);document.getElementById('navigation').replaceChildren(...controls);renderMoreNavigation();
}
function updateShell(key) {
  const page=pages[key];
  document.getElementById('page-title').textContent=page?.label||'Home';
  document.getElementById('page-kicker').textContent=pageHints[key]||'PRIVATE WORKSPACE';
}
function loadingView() {
  return el('section',{class:'skeleton-shell','aria-label':'Loading page'},
    el('div',{class:'skeleton skeleton-title'}),el('div',{class:'skeleton skeleton-line'}),el('div',{class:'skeleton skeleton-line short'}),
    el('div',{class:'skeleton-grid'},el('div',{class:'skeleton skeleton-card'}),el('div',{class:'skeleton skeleton-card'})));
}
export async function navigate(key=current) {
  if (!pages[key]) key='dashboard'; current=key; const seq=++generation;
  renderNavigation();updateShell(key);closeMore();
  const content=document.getElementById('content');content.setAttribute('aria-busy','true');content.replaceChildren(loadingView());
  try { const view=await pages[key].render(); if(seq===generation)content.replaceChildren(view); }
  catch(error){if(seq===generation)content.replaceChildren(card('Could not load this page',el('p',{},error.message),button('Retry',()=>navigate(key))));}
  finally{content.removeAttribute('aria-busy');}
  try { if(key==='dashboard')telegram?.BackButton?.hide();else telegram?.BackButton?.show(); } catch {}
}
async function start() {
  try { telegram?.ready(); telegram?.expand(); telegram?.BackButton?.onClick(()=>navigate('dashboard')); } catch {}
  document.getElementById('refresh').addEventListener('click',()=>navigate());
  document.getElementById('more-close').addEventListener('click',closeMore);
  try {
    const data=await api('/me'); state.user=data.user;
    document.getElementById('identity').textContent=data.user.name+' · Private bot chat';
    document.getElementById('connection-label').textContent='Live';
    document.querySelector('.connection-dot')?.classList.add('online');
    await import('./characters.js');
    await import('./models.js');
    await import('./management.js');
    await import('./memory.js');
    await import('./system.js');
    await navigate(telegram?.initDataUnsafe?.start_param || 'dashboard');
  } catch(error) {
    document.getElementById('identity').textContent='Authentication required';
    document.getElementById('connection-label').textContent='Auth required';
    document.querySelector('.connection-dot')?.classList.add('error');
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
