import {api,createSessionScope,registerPage,runJob,navigate} from './app.js';
import {el,card,button,field,empty,confirmAction,notice,sessionContext,feedback} from './ui.js';

const label=value=>String(value||'Unknown').replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());

async function renderDirector() {
  const root=el('div',{class:'director-room-page'});
  async function load(savedMessage='') {
    const data=await api('/director');
    const scope=createSessionScope(),sessionBody=scope.body;scope.set(data.session);
    const body=extra=>sessionBody({revision:data.revision,...extra});
    const operation=el('section',{class:'director-operation','aria-label':'Director operation',hidden:!savedMessage},
      savedMessage?feedback(savedMessage,'success'):null);
    const mutations=[];
    let reviewRequired=false;
    function lockMutations() {
      reviewRequired=true;
      for(const control of mutations){control.setAttribute('aria-disabled','true');control.disabled=true;}
    }
    function refreshButton() {
      return button('Refresh Director Room',async()=>{
        try {await load();}
        catch(error){operation.append(feedback('The saved view could not be refreshed. '+error.message));}
      },'secondary');
    }
    function showFailure(error) {
      const uncertain=error.uncertain===true,stale=error.status===409;
      if(uncertain||stale)lockMutations();
      const identity=error.jobId;
      const recovery=uncertain?' The outcome is not confirmed. Open Operations before submitting this action again.'
        :stale?' Refresh and review the current story before saving again. Your edits are still shown here.':' Your edits are still shown here.';
      const message=feedback((error.message||'The action could not complete.')+recovery+(identity?' Operation: '+identity+'.':''));
      message.append(el('div',{class:'actions'},button('Open Operations',()=>navigate('system'),'secondary'),stale?refreshButton():null));
      operation.hidden=false;operation.replaceChildren(message);
      operation.scrollIntoView?.({block:'nearest'});
    }
    function mutationButton(title,request,success,confirmation='') {
      const control=button(title,async()=>{
        if(reviewRequired)return;
        if(confirmation&&!await confirmAction(confirmation))return;
        if(reviewRequired)return;
        operation.hidden=false;operation.replaceChildren();
        let result;
        try {result=await request(operation);}
        catch(error){showFailure(error);return;}
        const message=typeof success==='function'?success(result):success;
        lockMutations();notice(message);
        try {await load(message);}
        catch(error) {
          const completed=feedback(message+' The saved view could not be refreshed. '+error.message+' Refresh before making another change.','warning');
          completed.append(el('div',{class:'actions'},refreshButton(),button('Open Operations',()=>navigate('system'),'secondary')));
          operation.hidden=false;operation.replaceChildren(completed);
          operation.scrollIntoView?.({block:'nearest'});
        }
      });
      mutations.push(control);return control;
    }
    const intro=card('Current direction',el('span',{class:'badge director-private'},'Private plans'),
      el('p',{class:'muted'},'These are hidden plans, not story facts or character knowledge.'),
      el('p',{class:'director-direction'},data.direction||'No accepted direction yet.'),
      el('div',{class:'director-meta'},el('p',{},'Phase: '+label(data.phase)),
        el('p',{},'Scene: '+(data.scene.scene_id||'Not initialized')+' · Thread: '+(data.scene.thread_id||'Not set')),
        el('p',{},'Viewpoint: '+(data.scene.viewpoint||'Not set')+' · '+(data.scene.pov||'POV not set'))));
    intro.classList.add('director-overview');
    if(data.degraded)intro.append(feedback('Director planning is degraded. Review the latest decision for details.','warning'));
    if(!data.mutable)intro.append(feedback('This story is closing or has ended. Director Room is read-only.','warning'));
    const decisions=data.history.map(item=>{
      const outcome=String(item.result||'unknown');
      const result=['accepted','rejected','failed'].includes(outcome)?outcome:'other';
      return el('article',{class:'decision-card','data-result':result},
        el('div',{class:'row spaced'},el('span',{class:'decision-status','data-result':result},label(outcome)),
          el('span',{class:'muted'},'Source: '+item.source)),
        item.direction?el('p',{class:'decision-direction'},item.direction):null,
        el('p',{class:'decision-reason'},item.reason||'No reason recorded.'));
    });
    const history=card('Decision history',el('p',{class:'muted'},'The Director’s recorded outcomes and reasons.'),
      decisions.length?el('div',{class:'decision-list'},...decisions.slice(0,3)):empty('No Director decisions yet.'));
    history.classList.add('director-decision-history');history.setAttribute('aria-label','Director decisions');
    if(decisions.length>3)history.append(el('details',{},el('summary',{},'Earlier decisions ('+(decisions.length-3)+')'),
      el('div',{class:'decision-list'},...decisions.slice(3))));
    const views=[sessionContext(data.session),intro,operation,history];
    if(data.mutable) {
      intro.append(mutationButton('Reassess now',target=>runJob('/director/reassess',body({confirm:true}),target),
        result=>result.message||'Director reassessment completed.',
        'Ask the Director model to reassess the current story? This uses the configured provider.'));
      const guidance=el('details',{class:'director-controls',open:true},el('summary',{},'Guide this story'));
      views.push(guidance);
      const direction=el('textarea',{value:data.direction||'',maxlength:4000,rows:5});
      const objective=el('textarea',{value:data.objective||'',maxlength:4000,rows:5});
      guidance.append(card('Next scene only',el('p',{class:'muted'},'A temporary instruction. It expires after the next scene transition; it does not decide your character’s actions.'),
        field('Next-scene direction',direction),mutationButton('Save next-scene direction',
          ()=>api('/director/direction',{method:'PATCH',body:body({scope:'next_scene',direction:direction.value})}),'Next-scene direction saved.')));
      guidance.append(card('Persistent objective',el('p',{class:'muted'},'Stays active until you change or clear it. This is the same objective as /group goal.'),
        field('Persistent objective',objective),mutationButton('Save objective',
          ()=>api('/director/direction',{method:'PATCH',body:body({scope:'persistent',direction:objective.value})}),'Director objective saved.')));
      const threads=el('select',{},...data.threads.filter(item=>item.status!=='resolved').map(item=>el('option',{value:item.thread_id},item.title+' · '+item.status)));
      if(threads.options.length)guidance.append(card('Next thread',el('p',{class:'muted'},'Steer the next scene without changing what has already happened.'),field('Thread',threads),
        mutationButton('Follow this thread',()=>api('/director/thread',{method:'POST',body:body({thread_id:threads.value})}),'Next thread planned.')));
      const cadence=el('select',{},...['adaptive','4','6','10','custom'].map(value=>el('option',{value},value==='adaptive'?'Adaptive':value==='custom'?'Custom interval':'Every '+value+' turns')));
      const interval=Number(data.settings.director_fixed_interval);
      cadence.value=data.settings.director_cadence_mode==='adaptive'?'adaptive':['4','6','10'].includes(String(interval))?String(interval):'custom';
      const custom=el('input',{type:'number',min:1,max:100,step:1,value:interval});
      const customField=field('Custom interval (1–100 turns)',custom);
      const sync=()=>{customField.hidden=cadence.value!=='custom';};cadence.addEventListener('change',sync);sync();
      const reasoning=el('input',{type:'number',min:0,max:32000,step:1,value:data.director_reasoning});
      views.push(el('details',{class:'director-controls'},el('summary',{},'Director settings'),
        card('Model and cadence',el('p',{class:'muted'},'Model: '+data.director_model+'. An unset Director route inherits Utility, then Story.'),
        button('Choose models',()=>navigate('models'),'secondary'),field('Reassessment cadence',cadence),customField,
        field('Director reasoning budget (0–32000)',reasoning),el('p',{class:'muted'},'Independent from Utility reasoning. Adaptive reassesses sooner around major events and the finale.'),
        mutationButton('Save Director settings',async()=>{
          const every=cadence.value==='custom'?Number(custom.value):cadence.value==='adaptive'?interval:Number(cadence.value);
          if(!Number.isInteger(every)||every<1||every>100||reasoning.value===''||!reasoning.checkValidity())throw new Error('Check the interval and reasoning budget.');
          return api('/director/controls',{method:'PATCH',body:body({cadence:cadence.value==='adaptive'?'adaptive':'fixed',interval:every,reasoning:Number(reasoning.value)})});
        },'Director settings saved.'))));
    }
    if(data.ending) {
      const progress=card('Story ending',el('p',{},'Mode: '+(data.ending.mode==='closed_story'?'Closed Story':'Open-ended')),
        el('p',{},'Progress: '+data.ending.lifecycle.replaceAll('_',' ')),
        el('p',{class:'muted'},'Closed Story ends with a separate epilogue. The completed original cannot be reopened or rewritten.'));
      if(data.ending.editable) {
        const mode=el('select',{},el('option',{value:'open_ended'},'Open-ended'),el('option',{value:'closed_story'},'Closed Story'));
        mode.value=data.ending.mode;
        const consent=el('select',{},el('option',{value:'false'},'Automatic when ready'),el('option',{value:'true'},'Ask before finale'));
        consent.value=String(data.ending.require_confirmation);
        progress.append(field('Story ending',mode),field('Finale entry',consent),mutationButton('Save ending settings',
          ()=>api('/director/ending-controls',{method:'PATCH',body:body({mode:mode.value,require_confirmation:consent.value==='true'})}),
          'Ending settings saved for this story.'));
      }
      if(data.ending.lifecycle==='finale_ready')progress.append(el('p',{},data.ending.reason),mutationButton('Begin finale',
        ()=>api('/director/begin-finale',{method:'POST',body:body({confirm:true,operation_id:crypto.randomUUID()})}),
        result=>result.message||'Finale started.','Save the pre-finale checkpoint and begin the finale? Earlier story events will be frozen.'));
      if(data.ending.recovery_needed||data.ending.delivery_pending)progress.append(mutationButton('Recover saved ending',
        target=>runJob('/director/recover-ending',body({confirm:true}),target),result=>result.message||'Ending recovery completed.',
        'Resume only the unfinished epilogue or delivery? Committed scenes will not be generated again.'));
      if(data.ending.has_resolution||data.ending.has_epilogue)progress.append(button('View ending',async()=>{
        const saved=await api('/director/ending');
        progress.append(el('div',{class:'saved-ending'},el('h3',{},'Saved ending'),el('p',{},saved.epilogue||saved.resolution)));
      },'secondary'));
      if(data.ending.alternate_available)progress.append(mutationButton('Alternate Ending',
        target=>runJob('/director/alternate-ending',body({confirm:true,checkpoint_id:data.ending.checkpoint_id}),target),
        result=>(result.message||'Alternate ending created.')+(result.memory_status==='degraded'?' External memory is unavailable; the copied transcript and local memories are usable.':''),
        'Create an independent story from the saved pre-finale checkpoint? The original ending will remain unchanged.'));
      if(data.ending.lifecycle==='closed')progress.append(button('New Story',()=>navigate('characters'),'secondary'));
      views.push(progress);
      const ending=card('Ending goal',el('p',{class:'muted'},'A hidden destination, not a predetermined script. A blank goal lets the ending emerge from the story.'));
      if(data.mutable&&data.ending.editable) {
        const goal=el('textarea',{value:data.ending.goal||'',maxlength:4000,rows:4});
        ending.append(field('Optional ending goal',goal),mutationButton('Save ending goal',
          ()=>api('/director/ending-goal',{method:'PATCH',body:body({goal:goal.value})}),'Ending goal saved.'));
      } else ending.append(el('p',{},data.ending.goal||'Emergent ending'),el('p',{class:'muted'},'The goal is locked once the finale starts.'));
      for(const item of data.ending.history)ending.append(el('details',{},el('summary',{},'Revision '+item.revision+' · '+item.source),
        el('p',{},'Before: '+(item.previous||'Emergent ending')),el('p',{},'After: '+(item.goal||'Emergent ending')),el('p',{},item.reason)));
      views.push(ending);
    }
    const arcs=card('Story arcs',el('p',{class:'muted'},'Statuses reflect committed story events. Your guidance changes plans, not facts.'));
    for(const arc of data.arcs||[]) {
      const item=card(arc.title,el('p',{},arc.status+' · '+arc.phase+' · '+arc.importance),el('p',{},arc.summary));
      if(data.mutable) {
        const note=el('textarea',{value:arc.guidance||'',maxlength:1000,rows:3});
        item.append(field('Guidance for '+arc.title,note),mutationButton('Save arc guidance',
          ()=>api('/director/arc-guidance',{method:'PATCH',body:body({arc_id:arc.arc_id,direction:note.value})}),'Arc guidance saved.'));
      } else item.append(el('p',{},arc.guidance||'No manual guidance.'));
      arcs.append(item);
    }
    if(!(data.arcs||[]).length)arcs.append(empty('No story arcs have been established yet.'));
    views.push(el('details',{class:'director-controls'},el('summary',{},'Story arcs'),arcs));
    root.replaceChildren(...views);
  }
  await load();return root;
}
registerPage('director','Director Room',renderDirector);
