import {api,createSessionScope,registerPage,runJob,navigate} from './app.js';
import {el,card,button,field,empty,confirmAction,notice} from './ui.js';

async function renderDirector() {
  const root=el('div',{class:'director-room-page'});
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const data=await api('/director');scope.set(data.session);
    const body=extra=>sessionBody({revision:data.revision,...extra});
    const intro=card('Director Room',el('p',{class:'muted'},'Session: '+data.session.title),
      el('p',{},'These are hidden plans, not story facts or character knowledge.'),
      el('p',{},'Phase: '+data.phase.replaceAll('_',' ')),
      el('p',{},'Scene: '+(data.scene.scene_id||'Not initialized')+' · Thread: '+(data.scene.thread_id||'Not set')),
      el('p',{},'Viewpoint: '+(data.scene.viewpoint||'Not set')+' · '+(data.scene.pov||'POV not set')),
      el('p',{},'Current direction: '+(data.direction||'No accepted direction yet.')));
    if(data.degraded)intro.append(el('p',{role:'status'},'Director planning is degraded. Your saved story is unchanged.'));
    if(!data.mutable)intro.append(el('p',{role:'status'},'This story is closing or has ended. Director Room is read-only.'));
    const history=card('Decision history',...data.history.map(item=>card(item.source+' · '+item.result,
      el('p',{},item.direction||item.reason),item.direction?el('small',{},item.reason):null)));
    if(!data.history.length)history.append(empty('No Director decisions yet.'));
    const views=[intro];
    if(data.mutable) {
      intro.append(button('Reassess now',async()=>{
        if(!await confirmAction('Ask the Director model to reassess the current story? This uses the configured provider.'))return;
        const result=await runJob('/director/reassess',body({confirm:true}),root);
        await load();notice(result.message);
      }));
      const direction=el('textarea',{value:data.direction||'',maxlength:4000,rows:5});
      const objective=el('textarea',{value:data.objective||'',maxlength:4000,rows:5});
      views.push(card('Next scene only',el('p',{class:'muted'},'A temporary instruction. It expires after the next scene transition; it does not decide your character’s actions.'),
        field('Next-scene direction',direction),button('Save next-scene direction',async()=>{
          await api('/director/direction',{method:'PATCH',body:body({scope:'next_scene',direction:direction.value})});await load();notice('Next-scene direction saved.');
        })));
      views.push(card('Persistent objective',el('p',{class:'muted'},'Stays active until you change or clear it. This is the same objective as /group goal.'),
        field('Persistent objective',objective),button('Save objective',async()=>{
          await api('/director/direction',{method:'PATCH',body:body({scope:'persistent',direction:objective.value})});await load();notice('Director objective saved.');
        })));
      const threads=el('select',{},...data.threads.filter(item=>item.status!=='resolved').map(item=>el('option',{value:item.thread_id},item.title+' · '+item.status)));
      if(threads.options.length)views.push(card('Next thread',el('p',{class:'muted'},'Steer the next scene without changing what has already happened.'),field('Thread',threads),button('Follow this thread',async()=>{
        await api('/director/thread',{method:'POST',body:body({thread_id:threads.value})});await load();notice('Next thread planned.');
      })));
      const cadence=el('select',{},...['adaptive','4','6','10','custom'].map(value=>el('option',{value},value==='adaptive'?'Adaptive':value==='custom'?'Custom interval':'Every '+value+' turns')));
      const interval=Number(data.settings.director_fixed_interval);
      cadence.value=data.settings.director_cadence_mode==='adaptive'?'adaptive':['4','6','10'].includes(String(interval))?String(interval):'custom';
      const custom=el('input',{type:'number',min:1,max:100,step:1,value:interval});
      const customField=field('Custom interval (1–100 turns)',custom);
      const sync=()=>{customField.hidden=cadence.value!=='custom';};cadence.addEventListener('change',sync);sync();
      const reasoning=el('input',{type:'number',min:0,max:32000,step:1,value:data.director_reasoning});
      views.push(card('Director settings',el('p',{class:'muted'},'Model: '+data.director_model+'. An unset Director route inherits Utility, then Story.'),
        button('Choose models',()=>navigate('models'),'secondary'),field('Reassessment cadence',cadence),customField,
        field('Director reasoning budget (0–32000)',reasoning),el('p',{class:'muted'},'Independent from Utility reasoning. Adaptive reassesses sooner around major events and the finale.'),
        button('Save Director settings',async()=>{
          const every=cadence.value==='custom'?Number(custom.value):cadence.value==='adaptive'?interval:Number(cadence.value);
          if(!Number.isInteger(every)||every<1||every>100||reasoning.value===''||!reasoning.checkValidity())throw new Error('Check the interval and reasoning budget.');
          await api('/director/controls',{method:'PATCH',body:body({cadence:cadence.value==='adaptive'?'adaptive':'fixed',interval:every,reasoning:Number(reasoning.value)})});
          await load();notice('Director settings saved.');
        })));
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
        progress.append(field('Story ending',mode),field('Finale entry',consent),button('Save ending settings',async()=>{
          await api('/director/ending-controls',{method:'PATCH',body:body({mode:mode.value,require_confirmation:consent.value==='true'})});
          await load();notice('Ending settings saved for this story.');
        }));
      }
      if(data.ending.lifecycle==='finale_ready')progress.append(el('p',{},data.ending.reason),button('Begin finale',async()=>{
        if(!await confirmAction('Save the pre-finale checkpoint and begin the finale? Earlier story events will be frozen.'))return;
        const result=await api('/director/begin-finale',{method:'POST',body:body({confirm:true,operation_id:crypto.randomUUID()})});
        await load();notice(result.message);
      }));
      if(data.ending.recovery_needed||data.ending.delivery_pending)progress.append(button('Recover saved ending',async()=>{
        if(!await confirmAction('Resume only the unfinished epilogue or delivery? Committed scenes will not be generated again.'))return;
        const result=await runJob('/director/recover-ending',body({confirm:true}),root);await load();notice(result.message);
      }));
      if(data.ending.has_resolution||data.ending.has_epilogue)progress.append(button('View ending',async()=>{
        const saved=await api('/director/ending');
        progress.append(el('div',{class:'saved-ending'},el('h3',{},'Saved ending'),el('p',{},saved.epilogue||saved.resolution)));
      },'secondary'));
      if(data.ending.lifecycle==='closed')progress.append(button('New Story',()=>navigate('characters'),'secondary'));
      views.push(progress);
      const ending=card('Ending goal',el('p',{class:'muted'},'A hidden destination, not a predetermined script. A blank goal lets the ending emerge from the story.'));
      if(data.mutable&&data.ending.editable) {
        const goal=el('textarea',{value:data.ending.goal||'',maxlength:4000,rows:4});
        ending.append(field('Optional ending goal',goal),button('Save ending goal',async()=>{
          await api('/director/ending-goal',{method:'PATCH',body:body({goal:goal.value})});await load();notice('Ending goal saved.');
        }));
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
        item.append(field('Guidance for '+arc.title,note),button('Save arc guidance',async()=>{
          await api('/director/arc-guidance',{method:'PATCH',body:body({arc_id:arc.arc_id,direction:note.value})});await load();notice('Arc guidance saved.');
        }));
      } else item.append(el('p',{},arc.guidance||'No manual guidance.'));
      arcs.append(item);
    }
    if(!(data.arcs||[]).length)arcs.append(empty('No story arcs have been established yet.'));
    views.push(arcs);
    views.push(history);root.replaceChildren(...views);
  }
  await load();return root;
}
registerPage('director','Director Room',renderDirector);
