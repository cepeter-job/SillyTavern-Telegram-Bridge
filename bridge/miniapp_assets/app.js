import {el,card,button,notice,feedback} from './ui.js';
import {icon} from './icons.js';
import {createJobController} from './jobs.js';
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
  let result;
  try {result=await response.json();}
  catch {throw Object.assign(new Error('The bridge response could not be read.'),{status:response.ok?502:response.status});}
  if (!response.ok) { const error = new Error(result?.error?.message || 'Request failed.'); error.status=response.status; throw error; }
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
export function openChat() {
  if(typeof telegram?.close==='function')telegram.close();
  else notice('Continue in your Telegram bot chat.');
}

const pageInfo = {
  dashboard:{label:'Home',title:'Your story',module:'system',hint:'STORY DECK',description:'Pick up where you left off.'},
  characters:{label:'Characters',module:'characters',parent:'dashboard',hint:'YOUR CAST',description:'Choose a character for a new story.'},
  sessions:{label:'Sessions',module:'management',parent:'dashboard',hint:'YOUR STORIES',description:'Switch stories or create a new session.'},
  tools:{label:'Tools',module:'manage',parent:'dashboard',hint:'STORY WORKSPACE',description:'Direction, saved knowledge and creative tools.'},
  settings:{label:'Settings',module:'manage',parent:'dashboard',hint:'YOUR BRIDGE',description:'Model roles, generation and system controls.'},
  manage:{label:'Tools',module:'manage',parent:'tools',hint:'STORY WORKSPACE',description:'Direction, saved knowledge and creative tools.'},
  system:{label:'System',module:'system',parent:'settings',hint:'BRIDGE HEALTH',description:'Status, operations and verified updates.'},
  models:{label:'Models',module:'models',parent:'settings',hint:'MODEL ROLES',description:'See which model each task uses.'},
  personas:{label:'Personas',module:'management',parent:'tools',hint:'YOUR IDENTITY',description:'Choose who you are in each story.'},
  worlds:{label:'Worlds',module:'management',parent:'tools',hint:'LORE & CONTEXT',description:'Give your conversation a setting.'},
  director:{label:'Director Room',module:'director',parent:'dashboard',hint:'PRIVATE PLANNING',description:'Review decisions and guide the next scene.'},
  memory:{label:'Memory',module:'memory',parent:'tools',hint:'CONTINUITY',description:'Keep the details that matter.'},
  npcs:{label:'NPC Bank',module:'npcs',parent:'tools',hint:'SUPPORTING CAST',description:'Review persistent supporting-character state.'},
  trackers:{label:'Story trackers',module:'trackers',parent:'dashboard',hint:'READ-ONLY SNAPSHOT',description:'Saved relationships, goals and checks.'},
  check:{label:'Check',module:'manage',parent:'tools',hint:'TELEGRAM TOOL',description:'Resolve an action in the Telegram check panel.'},
  imagine:{label:'Imagine',module:'manage',parent:'tools',hint:'TELEGRAM TOOL',description:'Create a scene image from your bot chat.'},
  databank:{label:'Data Bank',module:'memory',parent:'tools',hint:'REFERENCE LIBRARY',description:'Ground replies in your documents.'},
  advanced:{label:'Advanced settings',module:'manage',parent:'settings',hint:'FINE TUNE',description:'Usage and workspace diagnostics.'},
  usage:{label:'Usage',module:'usage',parent:'settings',hint:'TOKEN INSIGHTS',description:'Know where your tokens go.'},
};
const pages = {};
const primaryPages = [['dashboard','Home'],['characters','Characters'],['tools','Tools'],['settings','Settings']];
const primaryKeys=new Set(primaryPages.map(([key])=>key));
const loadedModules = new Map();
async function loadPage(key) {
  const name=pageInfo[key].module;
  if(!loadedModules.has(name))loadedModules.set(name,import('./'+name+'.js').catch(error=>{loadedModules.delete(name);throw error;}));
  await loadedModules.get(name);
}
export function registerPage(key, label, render) { pages[key] = {label,render}; }
let current='dashboard', generation=0,pageController;

function goBack() {
  return navigate(pageInfo[current].parent||'dashboard');
}
function renderNavigation() {
  let group=current;
  while(!primaryKeys.has(group))group=pageInfo[group].parent||'dashboard';
  const controls=primaryPages.map(([key,label])=>{
    const control=button(label,()=>navigate(key),'nav-button');control.prepend(icon(key));control.setAttribute('data-page',key);
    if(key===group)control.setAttribute('aria-current','page');return control;
  });
  document.getElementById('navigation').replaceChildren(...controls);
}
function updateShell(key) {
  const page=pageInfo[key];
  document.body.dataset.page=key;
  document.getElementById('page-title').textContent=page.title||page.label;
  document.getElementById('page-kicker').textContent=page?.hint||'YOUR WORKSPACE';
  document.getElementById('page-description').textContent=page?.description||'';
  document.getElementById('page-actions').replaceChildren();
  const back=document.getElementById('page-back');
  back.hidden=primaryKeys.has(key);
  back.replaceChildren(icon('back'),document.createTextNode('Back to '+(pageInfo[page.parent]?.label||'Home')));
}
function loadingView() {
  return el('section',{class:'skeleton-shell','aria-label':'Loading page'},
    el('div',{class:'skeleton skeleton-title'}),el('div',{class:'skeleton skeleton-line'}),el('div',{class:'skeleton skeleton-line short'}),
    el('div',{class:'skeleton-grid'},el('div',{class:'skeleton skeleton-card'}),el('div',{class:'skeleton skeleton-card'})));
}
export async function navigate(key=current) {
  if (!Object.hasOwn(pageInfo,key)) key='dashboard';
  const changed=key!==current;current=key;const seq=++generation;
  pageController?.abort();const controller=new AbortController();pageController=controller;
  if(changed)selectionFeedback();
  renderNavigation();updateShell(key);
  const content=document.getElementById('content');content.setAttribute('aria-busy','true');content.inert=true;
  const loading=setTimeout(()=>{if(seq===generation)content.replaceChildren(loadingView());},120);
  try {
    await loadPage(key);
    if(seq!==generation)return;
    const view=await pages[key].render({signal:controller.signal});
    if(seq===generation){
      const actions=key==='dashboard'?view.querySelector('.dashboard-toolbar'):null;
      if(actions)document.getElementById('page-actions').replaceChildren(actions);
      content.replaceChildren(view);
      if(changed){window.scrollTo(0,0);document.getElementById('page-title').focus({preventScroll:true});}
    }
  } catch(error) {
    controller.abort();
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
  document.getElementById('page-back').addEventListener('click',goBack);
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
    notice(error.message,'error');
  }
}
start();

const jobController=createJobController({request:api,identity:()=>state.user?.id||'current-document'});
export const resumeJob=(id,target)=>displayJob(notify=>jobController.resume(id,notify),target);
export const runJob=(path,body,target)=>displayJob(notify=>jobController.run(path,body,notify),target);
async function displayJob(run,target) {
  document.querySelectorAll('.recovery-completed').forEach(node=>node.remove());
  const label=el('span',{class:'badge'},'Pending'),message=el('p',{},'Submitting the operation…');
  const progress=card('Operation',label,message,el('progress',{'aria-label':'Operation in progress'}));
  progress.classList.add('operation-status');progress.setAttribute('role','status');
  if(target)target.replaceChildren(progress);
  try {
    const result=await run(event=>{
      label.textContent=event.state==='queued'?'Queued':event.state==='running'?'Running':event.state;
      message.textContent=event.state==='reconnecting'?'Connection interrupted. Last observed: '+(event.lastState||'unknown')+'. Reconnecting to the same operation.'
        :event.state==='queued'?'Waiting for a worker.':'You may leave this page; saved work remains in Operations.';
    });
    label.textContent='Completed';message.textContent='The operation completed.';progress.querySelector('progress')?.remove();
    if(result!==undefined)progress.append(el('details',{},el('summary',{},'Result'),el('pre',{},JSON.stringify(result,null,2))));
    return result;
  } catch(error) {
    if(error.recoverable)error.resume=nextTarget=>resumeJob(error.operationId,nextTarget);
    const summary=error.uncertain?'Status unavailable. Unable to confirm whether the operation finished. Recovery checks the original operation without submitting another.':error.message;
    const controls=el('div',{class:'actions'},button('Open Operations',()=>navigate('system'),'secondary'));
    if(error.resume)showRecovery(error);
    progress.replaceChildren(el('h2',{},error.uncertain?'Status unavailable':'Operation failed'),feedback(summary),controls);
    throw error;
  }
}

function showRecovery(error) {
  const id='recovery-'+error.operationId;
  let panel=document.getElementById(id);
  if(!panel) {
    panel=el('section',{id,class:'card operation-recovery','aria-label':'Unconfirmed operation'});
    document.getElementById('content').before(panel);
  }
  const resume=button(error.jobId?'Continue tracking':'Recover original operation',async()=>{
    await resumeJob(error.operationId,panel);
    panel.classList.add('recovery-completed');
    panel.append(el('p',{},'The saved operation has been recovered. Refresh the current view when you are ready to review saved state.'),
      button('Refresh view (discard unsaved edits)',async()=>{await navigate();panel.remove();},'secondary'),
      button('Dismiss',()=>panel.remove(),'secondary'));
  },'secondary');
  panel.replaceChildren(el('h2',{},'Unconfirmed operation'),
    el('p',{},'Current status is unknown. Recovery checks the saved operation and never starts it again.'),resume,
    button('Open Operations',()=>navigate('system'),'secondary'));
}
