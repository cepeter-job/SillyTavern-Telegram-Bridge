import {api,state,createSessionScope,registerPage} from './app.js';
import {el,card,button,field,confirmAction,notice} from './ui.js';
async function renderModels() {
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load(query='') {
    const [catalog,generation,presets]=await Promise.all([api('/models?q='+encodeURIComponent(query)),api('/generation'),api('/generation/presets')]);
    scope.set(generation.session);
    const search=el('input',{type:'search',placeholder:'Provider or model name',value:query,maxlength:120});
    const models=card('Models',el('p',{class:'muted'},'Selections apply only to '+state.session.title+'. Provider credentials remain on the server.'),field('Search configured models',search),button('Search',()=>load(search.value),'secondary'));
    for(const [key,label] of [['story','Story model'],['utility','Utility model']]) {
      const select=el('select');
      if(key==='utility')select.append(el('option',{value:''},'Inherit Story model'));
      const list=[...catalog.models];
      if(catalog[key]&&!list.some(x=>x.id===catalog[key]))list.unshift({id:catalog[key],name:catalog[key],provider:'Current'});
      for(const item of list)select.append(el('option',{value:item.id},item.provider+' / '+item.name));
      select.value=catalog[key];
      models.append(field(label,select),button('Save '+key,async()=>{
        await api('/models',{method:'POST',body:sessionBody({target:key,model:select.value})});notice(label+' saved.');await load(query);
      }));
    }
    const settings=card('Generation',el('p',{class:'muted'},'All values are checked before saving; an invalid field does not partially change the session.'));
    const inputs={};
    for(const [key,label,min,max,step] of [
      ['temperature','Temperature',0,2,.05],['top_p','Top P',0,1,.05],['max_tokens','Maximum output tokens',1,16000,1],
      ['frequency_penalty','Frequency penalty',-2,2,.1],['presence_penalty','Presence penalty',-2,2,.1]]) {
      const input=el('input',{type:'number',min,max,step,value:generation.settings[key],required:true});inputs[key]=input;
      settings.append(field(label+' ('+min+'–'+max+')',input));
    }
    const reasoningLevels=[
      ['none','None (0)',0],['low','Low (1,024)',1024],['medium','Medium (4,096)',4096],
      ['high','High (8,192)',8192],['max','Max (16,384)',16384],
    ];
    const currentReasoning=Number(generation.settings.reasoning_budget);
    const reasoning=el('select',{},...reasoningLevels.map(([key,label])=>el('option',{value:key},label)),el('option',{value:'custom'},'Custom'));
    reasoning.value=reasoningLevels.find(([, ,budget])=>budget===currentReasoning)?.[0]||'custom';
    const customReasoning=el('input',{type:'number',min:0,max:32000,step:1,value:currentReasoning,required:true});
    const customReasoningField=field('Custom reasoning budget (0–32000)',customReasoning);
    const syncReasoning=()=>{customReasoningField.hidden=reasoning.value!=='custom';};
    reasoning.addEventListener('change',syncReasoning);syncReasoning();
    settings.append(field('Reasoning level',reasoning),customReasoningField);
    inputs.stop_sequences=el('textarea',{maxlength:404,value:generation.settings.stop_sequences||'',placeholder:'One stop sequence per line, up to four.'});
    settings.append(field('Stop sequences',inputs.stop_sequences),button('Save settings',async()=>{
      const values={};for(const [key,input] of Object.entries(inputs)) {
        if(key==='stop_sequences')values[key]=input.value;
        else {if(!input.checkValidity()||input.value==='')throw new Error('Check '+key.replaceAll('_',' ')+'.');values[key]=Number(input.value);}
      }
      if(reasoning.value==='custom') {
        if(!customReasoning.checkValidity()||customReasoning.value==='')throw new Error('Check custom reasoning budget.');
        values.reasoning_budget=Number(customReasoning.value);
      } else {
        values.reasoning_budget=reasoningLevels.find(([key])=>key===reasoning.value)[2];
      }
      await api('/generation',{method:'PATCH',body:sessionBody({settings:values})});notice('Generation settings saved.');
    }));
    const preset=el('select',{},...presets.presets.map(name=>el('option',{value:name},name)));
    const name=el('input',{maxlength:64,placeholder:'e.g. Cinematic',pattern:'[A-Za-z0-9_-]+'});
    const presetCard=card('Presets',field('Saved presets',preset),el('div',{class:'actions'},button('Apply',async()=>{
      if(!preset.value)throw new Error('Choose a preset.');await api('/generation/presets/'+encodeURIComponent(preset.value)+'/apply',{method:'POST',body:sessionBody()});await load(query);notice('Preset applied.');
    }),button('Delete',async()=>{
      if(!preset.value)return;if(!await confirmAction('Delete preset '+preset.value+'?'))return;
      await api('/generation/presets/'+encodeURIComponent(preset.value),{method:'DELETE',body:sessionBody({confirm:true})});await load(query);
    },'danger')),field('Save current server settings as',name),button('Save preset',async()=>{
      if(!name.checkValidity()||!name.value)throw new Error('Enter a valid preset name.');
      const exists=presets.presets.includes(name.value);if(exists&&!await confirmAction('Replace this preset?'))return;
      await api('/generation/presets',{method:'POST',body:sessionBody({name:name.value,confirm:exists})});await load(query);
    }));
    root.replaceChildren(models,settings,presetCard);
  }
  await load();return root;
}
registerPage('models','Models',renderModels);
