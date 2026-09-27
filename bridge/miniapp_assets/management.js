import {api,state,createSessionScope,registerPage,runJob} from './app.js';
import {el,card,button,field,empty,confirmAction,notice} from './ui.js';
const enc=encodeURIComponent;
async function renderSessions() {
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load(query='') {
    const data=await api('/sessions?q='+enc(query));scope.set(data.session);
    const filter=el('input',{type:'search',value:query,maxlength:120,placeholder:'Search session titles'});
    const list=el('div',{class:'grid'});
    for(const item of data.sessions) {
      const title=el('input',{value:item.title,maxlength:120});
      const active=item.session_id===state.session.session_id;
      list.append(card(item.title,el('p',{class:'muted'},item.character_file+' · '+item.model_id),active?el('span',{class:'badge'},'Active'):null,
        field('Session title',title),el('div',{class:'actions'},button('Rename',async()=>{
          await api('/sessions/'+enc(item.session_id),{method:'PATCH',body:sessionBody({title:title.value})});await load(query);
        },'secondary'),button('Open',async()=>{
          const result=await api('/sessions/'+enc(item.session_id)+'/select',{method:'POST',body:sessionBody()});scope.set(result.session);await load(query);
        }),button('Delete',async()=>{
          if(!await confirmAction('Delete '+item.title+' and its stored conversation? Active sessions cannot be deleted.'))return;
          await runJob('/sessions/'+enc(item.session_id)+'/delete',sessionBody({confirm:true}),root);await load(query);
        },'danger'))));
    }
    const name=el('input',{maxlength:120,placeholder:'New session title'});
    root.replaceChildren(card('Sessions',el('p',{class:'muted'},data.total+' sessions · Private to your Telegram chat.'),field('Find a session',filter),button('Search',()=>load(filter.value),'secondary')),
      list,card('Create session',field('Title',name),button('Create and open',async()=>{
        const result=await api('/sessions',{method:'POST',body:sessionBody({title:name.value,operation_id:crypto.randomUUID()})});scope.set(result.session);await load(query);notice('Session created. Use /start in Telegram to begin.');
      })));
    if(!data.sessions.length)list.append(empty('No matching sessions.'));
  }
  await load();return root;
}
async function renderPersonas() {
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const data=await api('/personas');scope.set(data.session);
    const list=el('div',{class:'grid'});
    for(const item of data.personas) {
      list.append(card(item.name,el('p',{},item.description),item.id===state.session.persona_id?el('span',{class:'badge'},'Active'):null,
        el('div',{class:'actions'},button('Select',async()=>{await api('/personas/select',{method:'POST',body:sessionBody({persona_id:item.id})});await load();}),
          button('Edit',()=>editor(item),'secondary'),button('Delete',async()=>{
            if(!await confirmAction('Delete shared persona '+item.name+'? Personas referenced by any session are protected.'))return;
            await api('/personas/'+enc(item.id),{method:'DELETE',body:sessionBody({digest:item.digest,confirm:true})});await load();
          },'danger'))));
    }
    root.replaceChildren(card('Personas',el('p',{class:'muted'},'Shared native personas. Selection applies to the current private session.'),el('div',{class:'actions'},button('Create',()=>editor()),button('Persona off',async()=>{
      await api('/personas/select',{method:'POST',body:sessionBody({persona_id:''})});await load();
    },'secondary'))),list);
    if(!data.personas.length)list.append(empty('Create a persona to describe your role.'));
  }
  async function editor(item=null) {
    const name=el('input',{value:item?.name||'',maxlength:120});
    const description=el('textarea',{value:item?.description||'',maxlength:4000});
    const id=el('input',{maxlength:64,placeholder:'letters-numbers-underscores'});
    root.replaceChildren(card(item?'Edit persona':'Create persona',item?null:field('Unique ID',id),field('Name',name),field('Description',description),el('div',{class:'actions'},button('Cancel',load,'secondary'),button('Save',async()=>{
      if(item)await api('/personas/'+enc(item.id),{method:'PATCH',body:sessionBody({name:name.value,description:description.value,digest:item.digest})});
      else await api('/personas',{method:'POST',body:sessionBody({logical_id:id.value,name:name.value,description:description.value})});
      await load();notice('Persona saved.');
    }))));
  }
  await load();return root;
}
async function renderWorlds() {
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const data=await api('/worlds');scope.set(data.session);
    const list=el('div',{class:'grid'}),selected=new Set(data.worlds.filter(x=>x.active).map(x=>x.filename));
    for(const item of data.worlds) {
      const check=el('input',{type:'checkbox',checked:item.active,onchange:()=>{if(check.checked)selected.add(item.filename);else selected.delete(item.filename);}});
      list.append(card(item.filename,field('Use in current session',check),el('div',{class:'actions'},button('View / edit',()=>editor(item.filename),'secondary'),button('Delete',async()=>{
        const info=await api('/worlds/'+enc(item.filename));
        if(!await confirmAction('Delete '+item.filename+'? Session-referenced worlds are protected and a backup is retained.'))return;
        await api('/worlds/'+enc(item.filename),{method:'DELETE',body:sessionBody({digest:info.digest,confirm:true})});await load();
      },'danger'))));
    }
    root.replaceChildren(card('World Info',el('p',{class:'muted'},'Native lorebooks are shared. Choose which files belong to this session.'),el('div',{class:'actions'},button('Create / upload',()=>editor()),button('Save selection',async()=>{
      await api('/worlds/select',{method:'POST',body:sessionBody({worlds:[...selected]})});await load();notice('World selection saved.');
    }),button('Disable all',async()=>{await api('/worlds/select',{method:'POST',body:sessionBody({worlds:[]})});await load();},'secondary'))),list);
    if(!data.worlds.length)list.append(empty('No World Info documents.'));
  }
  async function editor(filename=null) {
    const info=filename?await api('/worlds/'+enc(filename)):null;
    const name=el('input',{maxlength:120,value:filename||'',placeholder:'Garden.json',disabled:!!filename});
    const document=el('textarea',{value:JSON.stringify(info?.document||{entries:{}},null,2),maxlength:1048576,spellcheck:false,rows:18});
    const file=el('input',{type:'file',accept:'.json',onchange:async()=>{try{
      const upload=file.files[0];if(!upload)return;if(upload.size>1048576)throw new Error('World editor limit is 1 MB.');
      document.value=await upload.text();if(!filename)name.value=upload.name;
    }catch(error){notice(error.message);}}});
    root.replaceChildren(card(filename?'Edit '+filename:'Create World Info',field('Filename',name),field('Read JSON file',file),field('World Info JSON',document),el('div',{class:'actions'},button('Cancel',load,'secondary'),button('Save',async()=>{
      let value;try{value=JSON.parse(document.value);}catch{throw new Error('Enter valid JSON.');}
      if(filename&&!await confirmAction('Replace this World Info revision? A verified backup will be retained.'))return;
      await api(filename?'/worlds/'+enc(filename):'/worlds',{method:filename?'PATCH':'POST',body:sessionBody({...(filename?{digest:info.digest,confirm:true}:{filename:name.value}),document:value})});
      await load();notice('World Info saved.');
    }))));
  }
  await load();return root;
}
registerPage('sessions','Sessions',renderSessions);
registerPage('personas','Personas',renderPersonas);
registerPage('worlds','Worlds',renderWorlds);
