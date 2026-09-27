import {api,state,createSessionScope,registerPage,runJob,navigate} from './app.js';
import {el,card,button,field,empty,confirmAction,notice} from './ui.js';
const enc=encodeURIComponent;
const rankTiers=new Set(['S','A','B','C','D']);
let query='',offset=0,resumedProposal=null;
function rankVisual(rank) {
  const tier=String(rank||'').trim().toUpperCase();
  if(!rankTiers.has(tier))return el('span',{class:'badge'},rank||'Unranked');
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
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const data=await api('/characters?q='+enc(query)+'&offset='+offset); scope.set(data.session);
    const search=el('input',{type:'search',value:query,placeholder:'Search filenames',maxlength:120});
    const searchButton=button('Search',async()=>{query=search.value;offset=0;await load();});
    search.addEventListener('keydown',e=>{if(e.key==='Enter')searchButton.click();});
    const grid=el('div',{class:'grid'});
    for(const item of data.characters) {
      const image=el('img',{class:'portrait modern-portrait',alt:item.name,loading:'lazy',decoding:'async',referrerpolicy:'no-referrer'});
      const entry=card(item.name,image,el('div',{class:'row spaced'},el('small',{},item.filename),rankVisual(item.rank)));
      if(item.active)entry.append(el('p',{class:'muted'},'Active character'));
      if(item.unavailable)entry.append(el('p',{},'Card cannot be read.'));
      else {
        entry.append(el('div',{class:'actions'},button('Info',()=>details(item)),button('Use',async()=>{
          if(!await confirmAction('Create a new normal session with '+item.name+'? Existing conversations will not be changed.'))return;
          const result=await api('/characters/'+enc(item.filename)+'/select',{method:'POST',body:sessionBody({confirm:true})}); scope.set(result.session);notice(result.message);await load();
        }),button('Optimize',()=>optimizer(item),'secondary'),button('Delete',async()=>{
          if(!await confirmAction('Delete '+item.name+'? Active, default and referenced cards are protected.'))return;
          await api('/characters/'+enc(item.filename),{method:'DELETE',body:sessionBody({digest:item.digest,confirm:true})});await load();
        },'danger')));
        api('/characters/'+enc(item.filename)+'/portrait',{binary:true}).then(blob=>{
          if(!blob||blob.size===0)throw new Error('empty portrait');
          const url=URL.createObjectURL(blob);let revoked=false;const revoke=()=>{if(!revoked){revoked=true;URL.revokeObjectURL(url);}};
          image.onload=revoke;image.onerror=()=>{revoke();portraitUnavailable(image);};image.src=url;
        }).catch(()=>portraitUnavailable(image));
      }
      grid.append(entry);
    }
    const file=el('input',{type:'file',accept:'.png'});
    const upload=card('Upload character',el('p',{class:'muted'},'SillyTavern PNG, up to 10 MB. Replacing an existing card requires a preview and explicit Apply.'),file,button('Upload',async()=>{
      const selected=file.files[0];if(!selected)throw new Error('Choose a PNG card first.');if(selected.size>10485760)throw new Error('Card must be 10 MB or smaller.');
      const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Could not read file.'));reader.readAsDataURL(selected);});
      const result=await api('/characters',{method:'POST',body:sessionBody({filename:selected.name,data:encoded})});
      if(result.nonce)await preview(result);else {notice('Character installed.');await load();}
    }));
    root.replaceChildren(card('Characters',el('p',{class:'muted'},data.total+' cards · '+data.session.title),field('Find a character',search),searchButton),grid);
    if(!data.characters.length)grid.append(empty('No matching character cards.'));
    const pager=el('div',{class:'actions'});
    if(offset>0)pager.append(button('Previous',async()=>{offset=Math.max(0,offset-24);await load();},'secondary'));
    if(offset+24<data.total)pager.append(button('Next',async()=>{offset+=24;await load();},'secondary'));
    root.append(pager,upload);
  }
  async function details(item) {
    const info=await api('/characters/'+enc(item.filename));
    const entries=Object.entries(info.fields).map(([key,value])=>
      el('details',{},el('summary',{},key.replaceAll('_',' ')),
        el('pre',{},typeof value==='string'?value:JSON.stringify(value,null,2))));
    root.replaceChildren(card(info.fields.name||item.name,button('Back to characters',load,'secondary'),...entries));
  }
  async function preview(result) {
    const fields=Object.keys(result.fields||{});
    const content=card('Review '+(result.kind==='upload'?'replacement':'optimization'),el('p',{},'The installed card is unchanged until you apply this preview.'));
    for(const key of fields)content.append(el('h3',{},key.replaceAll('_',' ')),el('div',{class:'grid'},card('Original',el('pre',{},result.original?.[key]||'—')),card('Proposed',el('pre',{},typeof result.fields[key]==='string'?result.fields[key]:JSON.stringify(result.fields[key],null,2)))));
    content.append(el('div',{class:'actions'},button('Discard',async()=>{await api('/character-proposals/'+enc(result.nonce)+'/discard',{method:'POST',body:sessionBody()});await load();},'secondary'),button('Apply',async()=>{
      if(!await confirmAction('Apply this exact preview? A verified backup is retained.'))return;
      const applied=await api('/character-proposals/'+enc(result.nonce)+'/apply',{method:'POST',body:sessionBody({confirm:true,action:result.kind==='upload'?'overwrite':'apply'})});notice(applied.message);await load();
    })));
    root.replaceChildren(content);
  }
  async function optimizer(item) {
    const guidance=el('textarea',{maxlength:2000,placeholder:'Optional: clarify motivation, reduce repetition, preserve the setting…'});
    root.replaceChildren(card('Character optimizer',el('p',{},item.name),el('p',{class:'muted'},'Uses your configured Utility model. Review all changes before applying; the installed card remains unchanged while generating.'),field('Manual suggestion (optional)',guidance),el('div',{class:'actions'},button('Back',load,'secondary'),button('Generate preview',async()=>{
      const result=await runJob('/characters/'+enc(item.filename)+'/optimize',sessionBody({digest:item.digest,suggestion:guidance.value}),root);await preview(result);
    }))));
  }
  if(resumedProposal) {
    const result=resumedProposal;resumedProposal=null;
    scope.set((await api('/session')).session);
    await preview(result);
  } else await load();
  return root;
}
registerPage('characters','Characters',renderCharacters);
