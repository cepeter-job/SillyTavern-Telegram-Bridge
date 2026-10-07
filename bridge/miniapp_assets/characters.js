import {api,state,createSessionScope,registerPage,runJob,navigate} from './app.js';
import {el,card,button,field,empty,confirmAction,notice,sessionContext,feedback} from './ui.js';
import {icon} from './icons.js';
const enc=encodeURIComponent;
const rankTiers=new Set(['S','A','B','C','D']);
let query='',offset=0,resumedProposal=null;
function rankVisual(rank) {
  const tier=String(rank||'').trim().toUpperCase();
  if(!tier)return null;
  if(!rankTiers.has(tier))return null;
  if(typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)return el('span',{class:'badge'},tier);
  const slot=el('span',{class:'rank-slot'}),video=el('video',{class:'rank-video',width:40,height:40,'aria-label':'Rank '+tier}),source=el('source',{src:'/miniapp/ranks/rank_'+tier+'.webm',type:'video/webm'});
  video.autoplay=true;video.loop=true;video.muted=true;video.playsInline=true;
  const fallback=()=>slot.replaceChildren(el('span',{class:'badge'},tier));
  source.addEventListener('error',fallback,{once:true});video.addEventListener('error',fallback,{once:true});
  video.append(source);slot.append(video);return slot;
}
function portraitUnavailable(image) { image.classList.add('portrait-broken');image.alt='Portrait unavailable';image.removeAttribute('src'); }
export async function reopenCharacterPreview(result) { resumedProposal=result; await navigate('characters'); }
async function renderCharacters() {
  const root=el('div',{class:'characters-page'});
  let readGeneration=0,currentView;
  function createView(session) {
    const scope=createSessionScope();scope.set(session);
    const controls=[],status=el('div',{class:'character-feedback'});
    let context=sessionContext(session),locked=false;
    const view={
      session,body:scope.body,status,
      active:()=>currentView===view&&!locked,
      lock() {locked=true;for(const control of controls) {control.setAttribute('aria-disabled','true');control.disabled=true;}},
      control(label,action,kind='') {
        const control=button(label,()=>{if(view.active())return action();},kind);controls.push(control);return control;
      },
      async confirm(message) {
        if(!await confirmAction(message))return false;
        if(!view.active()) {notice('This view has changed. Refresh and review the current story before making changes.','error');return false;}
        return true;
      },
      show(...children) {
        ++readGeneration;currentView?.lock();currentView=view;
        root.replaceChildren(context,status,...children);
      },
      updateContext(nextSession) {
        state.session=nextSession;
        const next=sessionContext(nextSession);context.replaceWith(next);context=next;
      },
      async saved(message,nextSession) {
        ++readGeneration;view.lock();const visible=currentView||view;visible.lock();
        if(nextSession)visible.updateContext(nextSession);
        notice(message);
        try {await load();}
        catch(error) {
          visible.status.replaceChildren(feedback(message+' '+(error.message||'The current characters could not be loaded.')+' Refresh and review before making more changes.'),
            button('Refresh and review',load,'secondary'));
        }
      },
    };
    return view;
  }
  async function load() {
    const generation=++readGeneration;
    const data=await api('/characters?q='+enc(query)+'&offset='+offset);
    if(generation!==readGeneration)return;
    const view=createView(data.session),sessionBody=view.body;
    const search=el('input',{type:'search',value:query,placeholder:'Search characters',maxlength:120,'aria-label':'Search characters'});
    const searchButton=view.control('Search',async()=>{query=search.value;offset=0;await load();},'secondary icon-button');searchButton.replaceChildren(icon('search'));searchButton.setAttribute('aria-label','Search characters');
    search.addEventListener('keydown',e=>{if(e.key==='Enter')searchButton.click();});
    const file=el('input',{type:'file',accept:'.png',class:'character-file-input',hidden:true,'aria-label':'Character PNG'});
    const add=view.control('Add character',()=>file.click(),'character-add');add.replaceChildren('+');add.setAttribute('aria-label','Add character');
    async function uploadSelected() {
      const selected=file.files[0];if(!selected)return;if(selected.size>10485760)throw new Error('Card must be 10 MB or smaller.');
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Could not read file.'));reader.readAsDataURL(selected);});
      if(!view.active())return;
      const result=await api('/characters',{method:'POST',body:sessionBody({filename:selected.name,data:encoded})});
      if(result.nonce)await preview(result,view.session);else await view.saved('Character installed.');
    }
    file.addEventListener('change',async()=>{
      if(!view.active()||add.getAttribute('aria-busy')==='true')return;
      add.disabled=true;add.setAttribute('aria-busy','true');
      try{await uploadSelected();}catch(error){notice((error.message||'Upload failed.')+(error.status===409?' Refresh and review the current story before uploading again.':''),'error');}
      finally{file.value='';add.disabled=add.getAttribute('aria-disabled')==='true';add.removeAttribute('aria-busy');}
    });
    async function restoreBackups() {
      const generation=++readGeneration,data=await api('/character-backups');
      if(generation!==readGeneration)return;
      const backupsView=createView(data.session),backupBody=backupsView.body;
      const content=card('Restore character backups',backupsView.control('Back to characters',load,'secondary'));
      if(!data.backups.length)content.append(empty('No verified character backups are available yet.'));
      for(const item of data.backups) {
        const status=item.matches_installed
          ? el('p',{class:'muted'},'Installed card already matches this backup.')
          : el('p',{class:'muted'},item.installed?'Installed card will be backed up before restore. Restoring again afterward will undo this change.':'Character is currently deleted.');
        const action=item.matches_installed
          ? null
          : backupsView.control('Restore backup',async()=>{
              if(!await backupsView.confirm('Restore the latest verified backup for '+item.name+'?'))return;
              const result=await api('/character-backups/'+enc(item.filename)+'/restore',{method:'POST',body:backupBody({digest:item.digest,backup_digest:item.backup_digest,confirm:true})});
              await backupsView.saved(result.message||'Character backup restored.',result.session);
            },'danger');
        content.append(card(item.name,status,action));
      }
      backupsView.show(content);
    }
    const restore=view.control('Restore backups',restoreBackups,'secondary');
    const header=el('div',{class:'character-page-header'},el('div',{},el('span',{class:'eyebrow'},'YOUR CAST'),el('h2',{},data.total+' Characters'),el('p',{class:'muted'},'Find a character. Start something new.')),el('div',{class:'actions'},restore,add));
    const searchToolbar=el('div',{class:'character-search'},search,searchButton);
    const grid=el('div',{class:'character-grid'});
    for(const item of data.characters) {
      const image=el('img',{class:'portrait modern-portrait',alt:item.name,loading:'lazy',decoding:'async',referrerpolicy:'no-referrer'});
      const portrait=el('div',{class:'portrait-frame'},image,el('div',{class:'rank-overlay'},rankVisual(item.rank)),item.active?el('span',{class:'active-chip'},'Active'):null);
      const body=el('div',{class:'character-card-body'},el('div',{class:'character-card-heading'},el('div',{},el('h2',{},item.name),el('small',{},item.filename))));
      const entry=el('section',{class:'card character-card'+(item.active?' is-active':'')},portrait,body);
      if(item.unavailable){portraitUnavailable(image);body.append(el('p',{class:'muted'},'Card cannot be read.'));}
      else {
        const use=view.control('Start new story',async()=>{
          if(!await view.confirm('Start a new normal story with '+item.name+'? Existing conversations will not be changed.'))return;
          const result=await api('/characters/'+enc(item.filename)+'/select',{method:'POST',body:sessionBody({confirm:true})});await view.saved(result.message||'New story started.',result.session);
        },'character-use');
        const menu=el('details',{class:'character-actions-menu'},el('summary',{'aria-label':'More actions'},'•••'),el('div',{class:'character-actions-popover'},
          view.control('Info',()=>details(item,view.session),'secondary'),view.control('Optimize',()=>optimizer(item,view.session),'secondary'),view.control('Delete',async()=>{
            if(!await view.confirm('Delete '+item.name+'? Active, default and referenced cards are protected.'))return;
            await api('/characters/'+enc(item.filename),{method:'DELETE',body:sessionBody({digest:item.digest,confirm:true})});await view.saved('Character deleted.');
          },'danger')));
        body.append(el('div',{class:'character-primary-actions'},use,menu));
        api('/characters/'+enc(item.filename)+'/portrait',{binary:true}).then(blob=>{
          if(!blob||blob.size===0)throw new Error('empty portrait');
          const url=URL.createObjectURL(blob);let revoked=false;const revoke=()=>{if(!revoked){revoked=true;URL.revokeObjectURL(url);}};
          image.onload=revoke;image.onerror=()=>{revoke();portraitUnavailable(image);};image.src=url;
        }).catch(()=>portraitUnavailable(image));
      }
      grid.append(entry);
    }
    view.show(header,file,searchToolbar,grid);
    if(!data.characters.length)grid.append(empty('No matching character cards.'));
    const pager=el('div',{class:'actions'});
    if(offset>0)pager.append(view.control('Previous',async()=>{offset=Math.max(0,offset-24);await load();},'secondary'));
    if(offset+24<data.total)pager.append(view.control('Next',async()=>{offset+=24;await load();},'secondary'));
    root.append(pager);
  }
  async function details(item,session) {
    const generation=++readGeneration,info=await api('/characters/'+enc(item.filename));
    if(generation!==readGeneration)return;
    const view=createView(session);
    const entries=Object.entries(info.fields).map(([key,value])=>
      el('details',{},el('summary',{},key.replaceAll('_',' ')),
        el('pre',{},typeof value==='string'?value:JSON.stringify(value,null,2))));
    view.show(card(info.fields.name||item.name,view.control('Back to characters',load,'secondary'),...entries));
  }
  async function preview(result,session) {
    const view=createView(session),sessionBody=view.body;
    const fields=Object.keys(result.fields||{});
    const content=card('Review '+(result.kind==='upload'?'replacement':'optimization'),el('p',{},'The installed card is unchanged until you apply this preview.'));
    for(const key of fields)content.append(el('h3',{},key.replaceAll('_',' ')),el('div',{class:'grid'},card('Original',el('pre',{},result.original?.[key]||'—')),card('Proposed',el('pre',{},typeof result.fields[key]==='string'?result.fields[key]:JSON.stringify(result.fields[key],null,2)))));
    content.append(el('div',{class:'actions'},view.control('Discard',async()=>{await api('/character-proposals/'+enc(result.nonce)+'/discard',{method:'POST',body:sessionBody()});await view.saved('Preview discarded.');},'secondary'),view.control('Apply',async()=>{
      if(!await view.confirm('Apply this exact preview? A verified backup is retained.'))return;
      const applied=await api('/character-proposals/'+enc(result.nonce)+'/apply',{method:'POST',body:sessionBody({confirm:true,action:result.kind==='upload'?'overwrite':'apply'})});await view.saved(applied.message||'Character changes applied.');
    })));
    view.show(content);
  }
  async function optimizer(item,session) {
    const view=createView(session),sessionBody=view.body;
    const guidance=el('textarea',{maxlength:2000,placeholder:'Optional: clarify motivation, reduce repetition, preserve the setting…'});
    const progress=el('div',{class:'character-operation'}),status=el('div');
    let uncertain=false;
    const back=view.control('Back',load,'secondary');
    const generate=view.control('Generate preview',async()=>{
      if(uncertain)return;
      status.replaceChildren();back.disabled=true;
      try {
        const result=await runJob('/characters/'+enc(item.filename)+'/optimize',sessionBody({digest:item.digest,suggestion:guidance.value}),progress);
        await preview(result,view.session);
      } catch(error) {
        progress.replaceChildren();
        uncertain=error.uncertain===true;
        const recovery=uncertain
          ? ' Your suggestion is kept. Open Operations to check the result before requesting another preview.'
          : error.status===409?' Your suggestion is kept. Refresh and review the current story before generating again.':' Your suggestion is kept for another attempt.';
        status.replaceChildren(feedback((error.message||'Could not generate the preview.')+recovery));
        if(uncertain) {
          generate.setAttribute('aria-disabled','true');
          status.append(button('Open operations',()=>navigate('system'),'secondary'));
        } else if(error.status===409)status.append(button('Refresh and review',load,'secondary'));
      } finally {back.disabled=back.getAttribute('aria-disabled')==='true';}
    });
    view.show(card('Character optimizer',el('p',{},item.name),el('p',{class:'muted'},'Uses the effective Utility model, which inherits Story when no Utility model is set. Review all changes before applying; the installed card remains unchanged while generating.'),field('Manual suggestion (optional)',guidance),el('div',{class:'actions'},back,generate),status,progress));
  }
  if(resumedProposal) {
    const result=resumedProposal;resumedProposal=null;
    await preview(result,(await api('/session')).session);
  } else await load();
  return root;
}
registerPage('characters','Characters',renderCharacters);
