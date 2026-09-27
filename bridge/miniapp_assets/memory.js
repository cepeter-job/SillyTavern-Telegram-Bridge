import {api,state,sessionBody,registerPage,runJob} from './app.js';
import {el,card,button,field,empty,confirmAction,notice} from './ui.js';
const enc=encodeURIComponent;
async function renderMemory() {
  const root=el('div');
  async function load() {
    const data=await api('/memory');state.session=data.session;
    const mode=el('select',{},el('option',{value:'on'},'On'),el('option',{value:'off'},'Off'));mode.value=data.mode;
    const summary=el('textarea',{value:data.summary,maxlength:12000,rows:9});
    const memoryCards=el('div'),editors=[];
    function addItem(item={key:'',kind:'fact',text:'',confidence:1}) {
      const key=el('input',{value:item.key,maxlength:80,placeholder:'relationship.sister'});
      const kind=el('select',{},...['fact','relationship','preference','promise','event','goal'].map(x=>el('option',{value:x},x)));kind.value=item.kind;
      const text=el('textarea',{value:item.text,maxlength:700,rows:3});
      const confidence=el('input',{type:'number',min:0,max:1,step:.05,value:item.confidence});
      const entry={key,kind,text,confidence,removed:false};editors.push(entry);
      const view=card(item.key||'New memory',field('Stable key',key),field('Kind',kind),field('Memory',text),field('Confidence (0–1)',confidence));
      view.append(button('Remove from draft',()=>{entry.removed=true;view.remove();},'danger'));memoryCards.append(view);
    }
    data.curated.forEach(addItem);
    const curator=card('Curated memory',el('p',{class:'muted'},'Edit the local list, then Save. Hindsight is a separate store: use Sync reviewed list to publish these changes. Clearing this list alone does not erase past Hindsight recall.'),memoryCards,
      el('div',{class:'actions'},button('Add memory',()=>{if(editors.filter(x=>!x.removed).length>=24)throw new Error('The list supports 24 memories.');addItem();},'secondary'),button('Save local list',async()=>{
        const items=editors.filter(x=>!x.removed).map(x=>({key:x.key.value,kind:x.kind.value,text:x.text.value,confidence:Number(x.confidence.value)}));
        await api('/memory/curated',{method:'PATCH',body:sessionBody({items,digest:data.curated_digest})});await load();notice('Local curated memory saved.');
      }),button('Curate new messages',async()=>{if(!await confirmAction('Use the Utility model to curate new transcript messages?'))return;await runJob('/memory/curated/generate',sessionBody(),root);await load();},'secondary'),
      button('Sync reviewed list',async()=>{if(!await confirmAction('Send the currently saved curated list to Hindsight? Unsaved edits are not included.'))return;await runJob('/memory/curated/sync',sessionBody({confirm:true}),root);await load();notice('Hindsight synchronization completed.');},'secondary')));
    const query=el('input',{maxlength:2000,placeholder:'Recall a person, promise, or event'}),results=el('div');
    root.replaceChildren(card('Memory',el('p',{class:'muted'},'Session: '+state.session.title+' · Recall scope: '+data.scope),field('Automatic memory',mode),button('Save memory mode',async()=>{await api('/memory/settings',{method:'PATCH',body:sessionBody({mode:mode.value})});notice('Memory mode saved.');})),
      card('Continuity summary',el('p',{class:'muted'},'A manual summary represents the current conversation. An empty value clears the local summary.'),field('Summary',summary),el('div',{class:'actions'},button('Save summary',async()=>{
        await api('/memory/summary',{method:'PATCH',body:sessionBody({summary:summary.value,digest:data.summary_digest})});await load();notice('Summary saved.');
      }),button('Regenerate with Utility',async()=>{if(!await confirmAction('Regenerate the continuity summary using the Utility model?'))return;await runJob('/memory/summary/generate',sessionBody(),root);await load();},'secondary'))),curator,
      card('Hindsight recall',field('Search session memories',query),button('Search',async()=>{const data=await runJob('/memory/search',sessionBody({query:query.value}),results);results.replaceChildren(...(data.results.length?data.results.map(x=>el('p',{},x)):[empty('No matching memories, or recall is unavailable.')]));}),results,
        button('Clear session Hindsight memory',async()=>{if(!await confirmAction('Permanently purge this session’s external Hindsight memory? The conversation transcript stays in the bridge.'))return;await runJob('/memory/purge',sessionBody({confirm:true}),root);await load();notice('Session memory purge completed.');},'danger')));
  }
  await load();return root;
}
async function renderDataBank() {
  const root=el('div');
  async function load() {
    const data=await api('/databank');state.session=data.session;
    const mode=el('select',{},el('option',{value:'on'},'On'),el('option',{value:'off'},'Off'));mode.value=data.mode;
    const documents=el('div',{class:'grid'});
    for(const item of data.documents)documents.append(card(item.filename,el('p',{class:'muted'},item.chunks+' chunks · '+item.bytes+' bytes'),el('div',{class:'actions'},button('Versions',()=>versions(item)),button('Reindex',async()=>{
      if(!await confirmAction('Reindex '+item.filename+' with the configured embedding backend?'))return;
      const result=await runJob('/databank/reindex',sessionBody({filename:item.filename,confirm:true}),root);await load();notice(result.indexed+'/'+result.total+' chunks indexed.');
    },'secondary'),button('Remove',async()=>{if(!await confirmAction('Remove all versions of '+item.filename+' from your Data Bank?'))return;
      await api('/databank/'+enc(item.filename),{method:'DELETE',body:sessionBody({confirm:true})});await load();
    },'danger'))));
    const file=el('input',{type:'file',accept:'.txt,.md,.markdown,.json,.yaml,.yml,.csv,.html,.htm,.xml,.docx,.pdf'});
    const query=el('input',{maxlength:2000,placeholder:'Search your documents'}),searchResults=el('div');
    root.replaceChildren(card('Data Bank',el('p',{class:'muted'},data.total+' documents · '+data.indexed+'/'+data.chunks+' chunks indexed. Documents are private to your bot chat, across its sessions.'),field('Use retrieval in conversations',mode),button('Save RAG mode',async()=>{await api('/databank/settings',{method:'PATCH',body:sessionBody({mode:mode.value})});notice('RAG mode saved.');})),documents,
      card('Upload document',el('p',{class:'muted'},'Supported text, PDF and DOCX documents, up to 10 MB. Uploading the same filename creates a version. Indexing may call the configured embedding backend.'),file,button('Upload and index',async()=>{
        const selected=file.files[0];if(!selected)throw new Error('Choose a document.');if(selected.size>10485760)throw new Error('Document exceeds 10 MB.');
        const bytes=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Cannot read file.'));reader.readAsDataURL(selected);});
        const result=await runJob('/databank',sessionBody({filename:selected.name,data:bytes}),root);await load();notice(result.chunks+' chunks processed.');
      })),card('Search Data Bank',field('Query',query),button('Search',async()=>{
        const result=await runJob('/databank/search',sessionBody({query:query.value}),searchResults);
        searchResults.replaceChildren(...(result.results.length?result.results.map(x=>card(x.filename,el('pre',{},x.text))):[empty('No matching chunks.')]));
      }),searchResults),button('Reindex all documents',async()=>{
        if(!await confirmAction('Reindex all Data Bank documents? This may use the embedding provider.'))return;
        const result=await runJob('/databank/reindex',sessionBody({confirm:true}),root);await load();notice(result.indexed+'/'+result.total+' chunks indexed.');
      },'secondary'));
    if(!data.documents.length)documents.append(empty('Upload a document to build your Data Bank.'));
  }
  async function versions(item) {
    const result=await api('/databank/'+enc(item.filename)+'/versions');
    root.replaceChildren(card('Versions · '+item.filename,button('Back to Data Bank',load,'secondary'),...result.versions.map(version=>card('Version '+version.version,
      el('p',{class:'muted'},version.chunks+' chunks · '+version.bytes+' bytes'),version.active?el('span',{class:'badge'},'Active'):button('Activate',async()=>{
        if(!await confirmAction('Use version '+version.version+' for retrieval?'))return;
        await api('/databank/'+enc(item.filename)+'/activate',{method:'POST',body:sessionBody({version:version.version,confirm:true})});await versions(item);
      })))));
  }
  await load();return root;
}
registerPage('memory','Memory',renderMemory);
registerPage('databank','Data Bank',renderDataBank);
