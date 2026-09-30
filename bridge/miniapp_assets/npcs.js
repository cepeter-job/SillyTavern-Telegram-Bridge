import {api,createSessionScope,registerPage,runJob} from './app.js';
import {el,card,button,empty,confirmAction,notice} from './ui.js';
const enc=encodeURIComponent;
let query='',offset=0;

async function renderNpcBank() {
  const root=el('div',{class:'npc-bank-page'});
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const data=await api('/npcs?q='+enc(query)+'&offset='+offset);scope.set(data.session);
    const search=el('input',{type:'search',value:query,placeholder:'Search NPCs',maxlength:120,'aria-label':'Search NPCs'});
    const searchButton=button('Search',async()=>{query=search.value;offset=0;await load();},'secondary');
    search.addEventListener('keydown',event=>{if(event.key==='Enter')searchButton.click();});
    const refresh=button('Refresh',async()=>{
      const result=await runJob('/npcs/refresh',sessionBody({}),root);
      notice('NPC Bank refreshed: '+result.updates+' updates');
      await load();
    },'secondary');
    const header=el('div',{class:'character-page-header'},
      el('div',{},el('span',{class:'eyebrow'},'SUPPORTING CAST'),el('h2',{},'NPC Bank'),el('p',{class:'muted'},'Persistent supporting-character state for this session.')),
      refresh);
    const grid=el('div',{class:'grid'});
    for(const item of data.npcs) {
      grid.append(card(item.name,
        item.aliases?.length?el('p',{class:'muted'},'Aliases: '+item.aliases.join(', ')):null,
        el('p',{class:'muted'},'Last seen row '+item.last_seen_rowid),
        button('Open dossier',()=>detail(item.npc_id))));
    }
    root.replaceChildren(header,el('div',{class:'character-search'},search,searchButton),grid);
    if(!data.npcs.length)grid.append(empty('No matching NPCs in this session.'));
    const pager=el('div',{class:'actions'});
    if(offset>0)pager.append(button('Previous',async()=>{offset=Math.max(0,offset-24);await load();},'secondary'));
    if(offset+24<data.total)pager.append(button('Next',async()=>{offset+=24;await load();},'secondary'));
    root.append(pager);
  }
  async function detail(npcId) {
    const data=await api('/npcs/'+enc(npcId));scope.set(data.session);
    const content=card(data.npc.name,button('Back to NPC Bank',load,'secondary'));
    if(data.npc.aliases?.length)content.append(el('p',{class:'muted'},'Aliases: '+data.npc.aliases.join(', ')));
    for(const [key,state] of Object.entries(data.fields)) {
      content.append(el('h3',{},key.replaceAll('_',' ')),el('pre',{},typeof state.value==='string'?state.value:JSON.stringify(state.value,null,2)));
    }
    content.append(button('History',()=>history(npcId),'secondary'));
    root.replaceChildren(content);
  }
  async function history(npcId) {
    const data=await api('/npcs/'+enc(npcId)+'/history');scope.set(data.session);
    const content=card('NPC history — '+data.npc.name,button('Back to dossier',()=>detail(npcId),'secondary'));
    if(!data.history.length)content.append(empty('No visible field history.'));
    for(const change of [...data.history].reverse()) {
      const row=el('div',{class:'card'},
        el('h3',{},change.field.replaceAll('_',' ')),
        el('p',{class:'muted'},'row '+change.source_rowid+' · '+change.operation),
        el('pre',{},JSON.stringify({before:change.before,after:change.after},null,2)));
      if(change.latest_for_field)row.append(button('Undo latest '+change.field.replaceAll('_',' '),async()=>{
        if(!await confirmAction('Undo the latest '+change.field.replaceAll('_',' ')+' change for '+data.npc.name+'?'))return;
        await api('/npcs/'+enc(npcId)+'/undo',{method:'POST',body:sessionBody({field:change.field,change_id:change.change_id,confirm:true})});
        notice('NPC field restored.');
        await detail(npcId);
      },'danger'));
      content.append(row);
    }
    root.replaceChildren(content);
  }
  await load();
  return root;
}
registerPage('npcs','NPC Bank',renderNpcBank);
