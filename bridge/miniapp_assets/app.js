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
  dashboard:{label:'Home',module:'system',hint:'YOUR STORY',description:'Continue where you left off.'},
  characters:{label:'Characters',module:'characters',hint:'YOUR CAST',description:'Find a character. Start something new.'},
  sessions:{label:'Sessions',module:'management',hint:'CONVERSATIONS',description:'Pick up where you left off.'},
  manage:{label:'Manage',module:'manage',hint:'YOUR WORKSPACE',description:'Shape the story around you.'},
  system:{label:'System',module:'system',hint:'BRIDGE HEALTH',description:'Status, operations and verified updates.'},
  models:{label:'Models',module:'models',hint:'GENERATION',description:'The right model for every task.'},
  personas:{label:'Personas',module:'management',hint:'YOUR IDENTITY',description:'Choose who you are in each story.'},
  worlds:{label:'Worlds',module:'management',hint:'LORE & CONTEXT',description:'Give your conversation a setting.'},
  director:{label:'Director Room',module:'director',hint:'STORY DIRECTION',description:'Review hidden plans and steer the next scene.'},
  memory:{label:'Memory',module:'memory',hint:'CONTINUITY',description:'Keep the details that matter.'},
  npcs:{label:'NPC Bank',module:'npcs',hint:'SUPPORTING CAST',description:'Review persistent supporting-character state.'},
  databank:{label:'Data Bank',module:'memory',hint:'REFERENCE LIBRARY',description:'Ground replies in your documents.'},
  advanced:{label:'Advanced settings',module:'manage',hint:'FINE TUNE',description:'Usage and workspace diagnostics.'},
  usage:{label:'Usage',module:'usage',hint:'TOKEN INSIGHTS',description:'Know where your tokens go.'},
};
const pages = {};
const primaryPages = [['dashboard','Home'],['characters','Characters'],['sessions','Sessions'],['manage','Manage'],['system','System']];
const managedPages = new Set(['manage','models','personas','worlds','memory','npcs','databank','advanced','usage','director']);
const loadedModules = new Map();
async function loadPage(key) {
  const name=pageInfo[key].module;
  if(!loadedModules.has(name))loadedModules.set(name,import('./'+name+'.js').catch(error=>{loadedModules.delete(name);throw error;}));
  await loadedModules.get(name);
}
export function registerPage(key, label, render) { pages[key] = {label,render}; }
let current='dashboard', generation=0;

function goBack() {
  if(managedPages.has(current)&&current!=='manage')return navigate('manage');
  return navigate('dashboard');
}
function renderNavigation() {
  const controls=primaryPages.map(([key,label])=>{
    const control=button(label,()=>navigate(key),'nav-button');control.prepend(icon(key));control.setAttribute('data-page',key);
    if(key===current||(key==='manage'&&managedPages.has(current)))control.setAttribute('aria-current','page');return control;
  });
  document.getElementById('navigation').replaceChildren(...controls);
}
function updateShell(key) {
  const page=pageInfo[key];
  document.body.dataset.page=key;
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
  renderNavigation();updateShell(key);
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
  try { telegram?.ready(); telegram?.expand(); telegram?.BackButton?.onClick(goBack); } catch {}
  document.getElementById('refresh').addEventListener('click',()=>navigate());
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
