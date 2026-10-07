import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage} from './dom.mjs';

const session={session_id:'story-a',title:'The lighthouse',character_file:'',model_id:'fixture::story',mode:'normal'};
const status={session,deployment:{version:'fixture',commit:''},telegram:{state:'observed'},database:{state:'ok',sessions:1,messages:0},uptime_seconds:60,installed_version:'fixture'};
const projection={session,relationships:[],agendas:[],inventory:[],skills:[],conditions:[],factions:[],quests:[],tasks:[],checks:[],last_updated_at:null,pending:false};
function apiFor(trackers=projection) {
  return async(path,{method='GET'}={})=>{
    assert.equal(method,'GET','Opening Home must not create a turn, tracker extraction or operation');
    const routes={'/status':status,'/sessions':{sessions:[session]},'/personas':{personas:[]},'/worlds':{worlds:[]},'/memory':{mode:'on'}};
    if(path==='/trackers'){if(trackers instanceof Error)throw trackers;return trackers;}
    assert.ok(Object.hasOwn(routes,path),'Only declared Home reads are allowed: '+path);return routes[path];
  };
}

test('Home distinguishes continuing a story from starting one and marks private/read-only destinations',async()=>{
  const page=await createPage('system.js',{api:apiFor()});
  try {
    const view=await page.pages.dashboard();page.document.getElementById('content').append(view);
    const buttons=[...view.querySelectorAll('button')];
    for(const label of ['Open chat','Switch session','Start new story','All sessions'])assert.ok(buttons.some(b=>b.textContent.trim()===label),'Action: '+label);
    assert.match(view.textContent,/Read-only/);assert.match(view.textContent,/Private/);
    for(const name of ['Check','Imagine']) {
      const action=buttons.find(b=>b.textContent.includes(name));assert.ok(action,name+' handoff');action.click();
    }
    assert.deepEqual(page.navigations,['check','imagine']);
  } finally {page.close();}
});

test('Home refuses a tracker summary from a different session',async()=>{
  const page=await createPage('system.js',{api:apiFor({...projection,session:{session_id:'story-b',title:'Another story'},checks:[{action:'Unrelated action'}]})});
  try {
    const view=await page.pages.dashboard();
    assert.match(view.textContent,/Tracker summary unavailable/);
    assert.doesNotMatch(view.textContent,/Another story|Unrelated action/);
    assert.match(view.textContent,/The lighthouse/);
  } finally {page.close();}
});

test('An optional tracker failure leaves the active story available',async()=>{
  const page=await createPage('system.js',{api:apiFor(new Error('offline'))});
  try {
    const view=await page.pages.dashboard();
    assert.match(view.textContent,/Tracker summary unavailable/);
    assert.ok([...view.querySelectorAll('button')].some(b=>b.textContent.trim()==='Open chat'));
  } finally {page.close();}
});
