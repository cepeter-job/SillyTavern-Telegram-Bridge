import {el,card,button,notice} from './ui.js';
import {icon} from './icons.js';
import {setupNative,selectionFeedback} from './native.js';
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

const pageInfo = {
  dashboard:{label:'Home',module:'system',hint:'YOUR WORKSPACE',description:'Your story, one tap away.'},
  characters:{label:'Characters',module:'characters',hint:'YOUR CAST',description:'Find a character. Start something new.'},
  usage:{label:'Usage',module:'usage',hint:'TOKEN INSIGHTS',description:'Know where your tokens go.'},
  sessions:{label:'Sessions',module:'management',hint:'CONVERSATIONS',description:'Pick up where you left off.'},
  models:{label:'Models',module:'models',hint:'GENERATION',description:'The right model for every task.'},
  memory:{label:'Memory',module:'memory',hint:'CONTINUITY',description:'Keep the details that matter.'},
  personas:{label:'Personas',module:'management',hint:'YOUR IDENTITY',description:'Choose who you are in each story.'},
  worlds:{label:'Worlds',module:'management',hint:'LORE & CONTEXT',description:'Give your conversation a setting.'},
  databank:{label:'Data Bank',module:'memory',hint:'REFERENCE LIBRARY',description:'Ground replies in your documents.'},
  system:{label:'System',module:'system',hint:'BRIDGE HEALTH',description:'Status, operations and verified updates.'},
};
const pages = {};
const primaryPages = [['dashboard','Home'],['characters','Characters'],['usage','Usage'],['sessions','Sessions']];
const secondaryPages = ['models','memory','personas','worlds','databank','system'];
const loadedModules = new Map();
async function loadPage(key) {
  const name=pageInfo[key].module;
  if(!loadedModules.has(name))loadedModules.set(name,import('./'+name+'.js').catch(error=>{loadedModules.delete(name);throw error;}));
  await loadedModules.get(name);
}
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
    const page=pageInfo[key];if(!page)return document.createDocumentFragment();
    const control=button(page.label,async()=>{closeMore();await navigate(key);},'more-link');
    control.prepend(icon(key));control.setAttribute('aria-label',page.label);control.append(el('small',{},page.description));
    control.hidden=!((page.label+' '+page.description).toLowerCase().includes((document.getElementById('more-search')?.value||'').toLowerCase()));
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
    const control=button(label,()=>navigate(key),'nav-button');control.prepend(icon(key));control.setAttribute('data-page',key);
    if(key===current)control.setAttribute('aria-current','page');return control;
  });
  const more=button('More',openMore,'nav-button');more.prepend(icon('more'));more.setAttribute('data-page','more');
  if(secondaryPages.includes(current))more.setAttribute('aria-current','page');
  controls.push(more);document.getElementById('navigation').replaceChildren(...controls);renderMoreNavigation();
}
function updateShell(key) {
  const page=pageInfo[key];
  document.getElementById('page-title').textContent=page?.label||'Home';
  document.getElementById('page-kicker').textContent=page?.hint||'YOUR WORKSPACE';
  document.getElementById('page-description').textContent=page?.description||'';
}
function loadingView() {
  return el('section',{class:'skeleton-shell','aria-label':'Loading page'},
    el('div',{class:'skeleton skeleton-title'}),el('div',{class:'skeleton skeleton-line'}),el('div',{class:'skeleton skeleton-line short'}),
    el('div',{class:'skeleton-grid'},el('div',{class:'skeleton skeleton-card'}),el('div',{class:'skeleton skeleton-card'})));
}
export async function navigate(key=current) {
  if (!Object.hasOwn(pageInfo,key)) key='dashboard';
  const changed=key!==current;current=key;const seq=++generation;
  if(changed)selectionFeedback();
  renderNavigation();updateShell(key);closeMore();
  const content=document.getElementById('content');content.setAttribute('aria-busy','true');content.inert=true;
  const loading=setTimeout(()=>{if(seq===generation)content.replaceChildren(loadingView());},120);
  try {
    await loadPage(key);
    if(seq!==generation)return;
    const view=await pages[key].render();
    if(seq===generation){content.replaceChildren(view);if(changed){window.scrollTo(0,0);document.getElementById('page-title').focus({preventScroll:true});}}
  } catch(error) {
    if(seq===generation)content.replaceChildren(card('Could not load this page',el('p',{},error.message),button('Retry',()=>navigate(key))));
  } finally {
    clearTimeout(loading);
    if(seq===generation){content.removeAttribute('aria-busy');content.inert=false;}
  }
  if(seq!==generation)return;
  try {if(key==='dashboard')telegram?.BackButton?.hide();else telegram?.BackButton?.show();}catch{}
}
async function start() {
  setupNative(telegram);
  document.getElementById('refresh').replaceChildren(icon('refresh'));
  document.getElementById('more-close').replaceChildren(icon('close'));
  document.getElementById('more-search').addEventListener('input',renderMoreNavigation);
  try { telegram?.ready(); telegram?.expand(); telegram?.BackButton?.onClick(()=>{if(document.getElementById('more-menu').open)closeMore();else navigate('dashboard');}); } catch {}
  document.getElementById('refresh').addEventListener('click',()=>navigate());
  document.getElementById('more-close').addEventListener('click',closeMore);
  try {
    const data=await api('/me'); state.user=data.user;
    document.getElementById('identity').textContent=data.user.name+' · Private bot chat';
    document.getElementById('connection-label').textContent='Connected';
    document.querySelector('.connection-dot')?.classList.add('online');
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
