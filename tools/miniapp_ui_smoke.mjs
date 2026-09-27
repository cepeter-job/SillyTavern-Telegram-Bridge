// Usage: MINIAPP_JSDOM_ROOT=/path/with/node_modules PYTHON=/path/to/python node --experimental-vm-modules tools/miniapp_ui_smoke.mjs
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import vm from 'node:vm';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {JSDOM}=await import(pathToFileURL(resolve(process.env.MINIAPP_JSDOM_ROOT||root,'node_modules/jsdom/lib/api.js')));
const child=spawn(process.env.PYTHON||'python3',[resolve(root,'tests/miniapp_browser_fixture.py')],{cwd:root,stdio:['ignore','pipe','inherit']});
const lines=createInterface({input:child.stdout});
const ready=await Promise.race([new Promise((ok,bad)=>{lines.once('line',line=>{try{ok(JSON.parse(line));}catch(e){bad(e);}});child.once('exit',code=>bad(new Error('Fixture exited '+code)));}),new Promise((_,bad)=>setTimeout(()=>bad(new Error('Fixture startup timed out')),10000).unref())]);
const html=await readFile(resolve(root,'bridge/miniapp_assets/index.html'),'utf8');
const dom=new JSDOM(html,{url:ready.url+'/miniapp/',runScripts:'outside-only',pretendToBeVisual:true});
const context=dom.getInternalVMContext(),errors=[];
process.on('unhandledRejection',error=>errors.push(error));
dom.window.Telegram={WebApp:{initData:ready.initData,initDataUnsafe:{start_param:'dashboard'},ready(){},expand(){},BackButton:{onClick(){},hide(){},show(){}}}};
dom.window.AbortSignal=globalThis.AbortSignal;
dom.window.URL.createObjectURL=()=> 'blob:test-fixture';
dom.window.URL.revokeObjectURL=()=>{};
dom.window.fetch=async (path,options)=>{
  assert.ok(String(path).startsWith('/api/v1/'),'Only local API requests are permitted');
  const response=await fetch(ready.url+path,options);
  if(!response.ok)errors.push(new Error('API '+response.status+' '+path));
  return response;
};
dom.window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
dom.window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
const modules=new Map();
async function moduleFor(name) {
  name=name.replace(/^\.\//,'');assert.match(name,/^[a-z]+\.js$/);
  if(modules.has(name))return modules.get(name);
  const source=await readFile(resolve(root,'bridge/miniapp_assets',name),'utf8');
  const module=new vm.SourceTextModule(source,{context,identifier:name,importModuleDynamically:async spec=>{
    const loaded=await moduleFor(spec);if(loaded.status==='unlinked')await loaded.link(linker);
    if(loaded.status==='linked')await loaded.evaluate();return loaded;
  }});
  modules.set(name,module);return module;
}
async function linker(spec){return moduleFor(spec);}
async function until(predicate,label) {
  for(let i=0;i<150;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20));}
  throw new Error('Timed out: '+label+' | '+dom.window.document.body.textContent.slice(-1000));
}
try {
  const app=await moduleFor('app.js');await app.link(linker);await app.evaluate();
  await until(()=>dom.window.document.querySelectorAll('nav button').length===9,'all nine pages registered');
  for(const page of ['dashboard','characters','models','sessions','personas','worlds','memory','databank','system']) {
    await app.namespace.navigate(page);
    const body=dom.window.document.querySelector('main').textContent;
    assert.ok(!body.includes('Could not load this page'),page+': '+body);
    assert.ok(body.length>30,page+' rendered content');
    console.log('render='+page+' ok');
  }
  await app.namespace.navigate('models');
  const document=dom.window.document;
  const label=[...document.querySelectorAll('label')].find(n=>n.textContent.startsWith('Temperature'));
  document.getElementById(label.htmlFor).value='0.65';
  [...document.querySelectorAll('main button')].find(n=>n.textContent==='Save settings').click();
  await until(()=>document.getElementById('notice').textContent==='Generation settings saved.','save settings');
  const stored=await app.namespace.api('/generation');assert.equal(stored.settings.temperature,.65);
  await app.namespace.navigate('sessions');
  const input=[...document.querySelectorAll('input')].find(n=>n.placeholder==='New session title');
  input.value='DOM verified session';
  [...document.querySelectorAll('main button')].find(n=>n.textContent==='Create and open').click();
  await until(()=>document.querySelector('main').textContent.includes('DOM verified session'),'create session');
  const sessions=await app.namespace.api('/sessions');assert.equal(sessions.session.title,'DOM verified session');
  const info=await app.namespace.api('/characters/Alice.png');
  let job=await app.namespace.api('/characters/Alice.png/optimize',{method:'POST',body:app.namespace.sessionBody({digest:info.digest,suggestion:'Clarify motivation.',operation_id:'dom-optimizer'})});
  for(let i=0;i<50&&['queued','running'].includes(job.state);i++) {
    await new Promise(r=>setTimeout(r,50));job=await app.namespace.api('/jobs/'+job.id);
  }
  assert.equal(job.state,'succeeded',JSON.stringify(job));
  await app.namespace.navigate('system');
  const resume=[...document.querySelectorAll('main button')].find(n=>n.textContent==='Review saved preview');
  assert.ok(resume,'Completed optimization must be resumable without another model call');
  resume.click();
  await until(()=>document.querySelector('main').textContent.includes('Review optimization'),'resume saved proposal');
  [...document.querySelectorAll('main button')].find(n=>n.textContent==='Apply').click();
  await until(()=>document.querySelector('dialog[open]'),'apply confirmation');
  [...document.querySelectorAll('dialog button')].find(n=>n.textContent==='Confirm').click();
  await until(()=>document.querySelector('main').textContent.includes('Upload character'),'proposal applied');
  const updated=await app.namespace.api('/characters/Alice.png');
  assert.equal(updated.fields.description,'A thoughtful companion with clear motivations and consistent habits.');
  assert.equal(errors.length,0,errors.map(e=>e.message).join('\n'));
  console.log('mutations=3 passed; optimizer-resume=passed; pages=9 passed; browser-errors=0');
} finally {
  dom.window.close();lines.close();child.kill('SIGTERM');
}
