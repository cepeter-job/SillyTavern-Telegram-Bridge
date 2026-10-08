// Isolated page rendering with explicit API boundaries; no network or bot access.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from 'jsdom';

export async function createPage(name,{api=async path=>{throw new Error('Unexpected API: '+path);},runJob=async()=>{throw new Error('Unexpected job');},state={session:null},telegram}={}) {
  const actualApp=name==='app.js';
  const html=actualApp?await readFile(new URL('../../bridge/miniapp_assets/index.html',import.meta.url),'utf8'):'<!doctype html><main id="content"></main><div id="notice" hidden></div>';
  const dom=new JSDOM(html,{url:'http://127.0.0.1/miniapp/',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom,document=window.document,pages={},navigations=[];
  window.Telegram={WebApp:telegram||{}};
  window.TextEncoder=globalThis.TextEncoder;
  Object.defineProperty(window.crypto,'subtle',{value:globalThis.crypto.subtle});
  window.URL.createObjectURL=()=> 'blob:isolated-fixture';
  window.URL.revokeObjectURL=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  window.scrollTo=()=>{};
  if(actualApp) {
    window.AbortSignal=globalThis.AbortSignal;
    window.AbortController=globalThis.AbortController;
    window.fetch=async(path,options)=>{
      assert.ok(String(path).startsWith('/api/v1/'),'Only the local API boundary is available');
      try {
        const value=await api(String(path).slice('/api/v1'.length),{...options,body:options.body?JSON.parse(options.body):undefined});
        return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
      } catch(error) {
        if(!error.status)throw error;
        return new Response(JSON.stringify({error:{message:error.message}}),{status:error.status,headers:{'Content-Type':'application/json'}});
      }
    };
  }
  const exports={api,runJob,state,registerPage:(key,label,render)=>{pages[key]=render;},navigate:async key=>{navigations.push(key);},
    sessionBody:(extra={})=>({session_id:state.session?.session_id,...extra}),
    createSessionScope:()=>{let sessionId='';return {set(session){sessionId=String(session?.session_id||'');state.session=session;},body(extra={}){if(!sessionId)throw new Error('Open or create a session first.');return {...extra,session_id:sessionId};}};},
    openChat:()=>{if(typeof window.Telegram.WebApp.close==='function')window.Telegram.WebApp.close();else navigations.push('telegram');}};
  const context=dom.getInternalVMContext(),modules=new Map();
  const app=new vm.SyntheticModule(Object.keys(exports),function(){for(const [key,value] of Object.entries(exports))this.setExport(key,value);},{context,identifier:'app.js'});
  if(!actualApp)modules.set('app.js',app);
  async function moduleFor(spec) {
    const key=spec.replace(/^\.\//,'');assert.match(key,/^[a-z]+\.js$/);
    if(modules.has(key))return modules.get(key);
    const source=await readFile(new URL('../../bridge/miniapp_assets/'+key,import.meta.url),'utf8');
    const loaded=new vm.SourceTextModule(source,{context,identifier:key,importModuleDynamically:async child=>{
      const mod=await moduleFor(child);if(mod.status==='unlinked')await mod.link(moduleFor);if(mod.status==='linked')await mod.evaluate();return mod;
    }});
    modules.set(key,loaded);return loaded;
  }
  const page=await moduleFor(name);await page.link(moduleFor);await page.evaluate();
  return {document,window,pages,navigations,namespace:page.namespace,close:()=>window.close()};
}

export async function until(predicate,label='UI update') {
  for(let i=0;i<100;i++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,5));}
  assert.fail('Timed out: '+label);
}
