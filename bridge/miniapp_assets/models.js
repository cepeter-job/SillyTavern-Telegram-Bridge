import {api,createSessionScope,registerPage,navigate} from './app.js';
import {el,card,button,field,confirmAction,notice,sessionContext,feedback} from './ui.js';
async function renderModels() {
  const root=el('div',{class:'models-page'});
  let loadGeneration=0,retireCurrent;
  async function load(query='') {
    const generationId=++loadGeneration;
    const [catalog,generation,presets]=await Promise.all([api('/models?q='+encodeURIComponent(query)),api('/generation'),api('/generation/presets')]);
    if(generationId!==loadGeneration)return;
    if(catalog.session.session_id!==generation.session.session_id) {
      const error=new Error('The active story changed while loading. Refresh and review its current models and settings.');error.status=409;throw error;
    }
    const scope=createSessionScope(),sessionBody=scope.body;scope.set(generation.session);
    const mutationControls=[];
    let reviewRequired=false;
    async function submit(action,target) {
      target.replaceChildren();
      try {await action();}
      catch(error) {
        const conflict=error.status===409;
        const message=(error.message||'Could not save changes.')+(conflict
          ? ' Refresh and review the current story before saving. Your entries are kept until you refresh.'
          : ' Your entries are kept. Review them and try again.');
        target.replaceChildren(feedback(message));
        if(conflict)target.append(button('Refresh and review',()=>load(query),'secondary'));
      }
    }
    function mutationButton(label,action,target,kind='') {
      const control=button(label,()=>{if(!reviewRequired)return submit(action,target);},kind);
      mutationControls.push(control);return control;
    }
    function lockMutations() {
      reviewRequired=true;
      for(const control of mutationControls) {control.setAttribute('aria-disabled','true');control.disabled=true;}
    }
    async function confirmCurrent(message) {
      if(!await confirmAction(message))return false;
      if(reviewRequired) {notice('This form has changed. Refresh and review the current story before making changes.','error');return false;}
      return true;
    }
    async function refreshSaved(message,target) {
      notice(message);lockMutations();
      try {await load(query);}
      catch(error) {
        target.replaceChildren(feedback(message+' '+(error.message||'The current settings could not be loaded.')+' Your entries are kept. Refresh and review before making more changes.'),
          button('Refresh and review',()=>load(query),'secondary'));
      }
    }
    const pageFeedback=el('div');
    const search=el('input',{type:'search',placeholder:'Provider or model name',value:query,maxlength:120});
    const searchButton=button('Search',()=>submit(()=>load(search.value),pageFeedback),'secondary');
    search.addEventListener('keydown',event=>{if(event.key==='Enter')searchButton.click();});
    const models=card('Model roles',el('p',{class:'muted'},'Choose a model for each task in this story.'),el('div',{class:'model-search'},field('Search configured models',search),searchButton),pageFeedback);
    models.classList.add('model-roles');
    const roles=[
      ['story','Story','Writes the conversation.','Use the server Story default'],
      ['utility','Utility','Handles supporting tasks and character optimization.','Inherit Story model'],
      ['director','Director','Plans the story privately.','Inherit Utility, then Story'],
    ];
    for(const [key,label,purpose,inherited] of roles) {
      const configured=catalog.configured[key]||'',effective=catalog[key]||'';
      const select=el('select',{required:key==='story'});
      if(key!=='story')select.append(el('option',{value:''},key==='director'?'Inherit Utility model':'Inherit Story model'));
      const list=[...catalog.models];
      const selected=configured||(key==='story'?effective:'');
      if(selected&&!list.some(item=>item.id===selected))list.unshift({id:selected,name:selected,provider:'Current selection'});
      for(const item of list)select.append(el('option',{value:item.id},item.provider+' / '+item.name));
      select.value=selected;
      const status=el('div');
      const route=el('div',{class:'model-route'},
        el('p',{},el('span',{class:'muted'},'Configured · '),el('strong',{},configured||inherited)),
        el('p',{class:'model-effective'},el('span',{class:'muted'},'Effective · '),el('strong',{},effective||'No model resolved')));
      if(configured&&configured!==effective)route.append(el('p',{class:'muted'},'The saved choice differs from the current resolved route. Review the configured model before saving.'));
      if(key==='director')route.append(el('p',{class:'muted'},'Inheritance: Director → Utility → Story.'));
      const row=el('section',{class:'model-role','data-role':key},
        el('div',{class:'model-role-heading'},el('h3',{},label),el('p',{class:'muted'},purpose)),route,
        el('div',{class:'model-controls'},field(label+' model',select),mutationButton('Save '+label,async()=>{
          if(!select.checkValidity())throw new Error('Choose a configured Story model.');
          await api('/models',{method:'POST',body:sessionBody({target:key,model:select.value})});await refreshSaved(label+' model saved.',status);
        },status)),status);
      models.append(row);
    }
    const settings=card('Generation',el('p',{class:'muted'},'Tune the Story response. All values are checked together before saving; an invalid field leaves saved settings unchanged.'));
    const generationGrid=el('div',{class:'generation-grid'}),generationFeedback=el('div');
    const inputs={};
    for(const [key,label,min,max,step] of [
      ['temperature','Temperature',0,2,.05],['top_p','Top P',0,1,.05],['max_tokens','Maximum output tokens',1,16000,1],
      ['frequency_penalty','Frequency penalty',-2,2,.1],['presence_penalty','Presence penalty',-2,2,.1]]) {
      const input=el('input',{type:'number',min,max,step,value:generation.settings[key],required:true});inputs[key]=input;
      generationGrid.append(field(label+' ('+min+'–'+max+')',input));
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
    generationGrid.append(field('Reasoning level',reasoning),customReasoningField);
    settings.append(generationGrid,el('div',{class:'models-secondary'},el('p',{class:'muted'},'Director reasoning has its own setting in the Director Room.'),button('Director reasoning',()=>navigate('director'),'secondary')));
    inputs.stop_sequences=el('textarea',{maxlength:404,value:generation.settings.stop_sequences||'',placeholder:'One stop sequence per line, up to four.'});
    settings.append(field('Stop sequences',inputs.stop_sequences),mutationButton('Save settings',async()=>{
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
    },generationFeedback),generationFeedback);
    const preset=el('select',{},...presets.presets.map(name=>el('option',{value:name},name)));
    const name=el('input',{maxlength:64,placeholder:'e.g. Cinematic',pattern:'[A-Za-z0-9_-]+'});
    const presetFeedback=el('div');
    const presetCard=card('Presets',el('p',{class:'muted'},'Presets save generation settings. Save your edits above before creating a preset.'),field('Saved presets',preset),el('div',{class:'actions'},mutationButton('Apply',async()=>{
      if(!preset.value)throw new Error('Choose a preset.');await api('/generation/presets/'+encodeURIComponent(preset.value)+'/apply',{method:'POST',body:sessionBody()});await refreshSaved('Preset applied.',presetFeedback);
    },presetFeedback),mutationButton('Delete',async()=>{
      if(!preset.value)return;if(!await confirmCurrent('Delete preset '+preset.value+'?'))return;
      await api('/generation/presets/'+encodeURIComponent(preset.value),{method:'DELETE',body:sessionBody({confirm:true})});await refreshSaved('Preset deleted.',presetFeedback);
    },presetFeedback,'danger')),field('Save current server settings as',name),mutationButton('Save preset',async()=>{
      if(!name.checkValidity()||!name.value)throw new Error('Enter a valid preset name.');
      const exists=presets.presets.includes(name.value);if(exists&&!await confirmCurrent('Replace this preset?'))return;
      await api('/generation/presets',{method:'POST',body:sessionBody({name:name.value,confirm:exists})});await refreshSaved('Preset saved.',presetFeedback);
    },presetFeedback),presetFeedback);
    retireCurrent?.();retireCurrent=lockMutations;
    root.replaceChildren(sessionContext(generation.session),models,settings,presetCard);
  }
  await load();return root;
}
registerPage('models','Models',renderModels);
