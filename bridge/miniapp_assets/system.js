import {api,state,createSessionScope,registerPage,navigate} from './app.js';
import {el,card,button,empty,confirmAction,notice} from './ui.js';
import {icon} from './icons.js';
function healthCard(iconName,title,...children) {
  const view=card(title,...children),heading=view.querySelector('h2'),mark=icon(iconName);
  mark.classList.add('health-icon');mark.dataset.healthIcon=iconName;
  heading.classList.add('health-card-title');heading.prepend(mark);
  return view;
}
function memoryStateLabel(value) {
  const state=String(value||'disabled');return state.charAt(0).toUpperCase()+state.slice(1);
}
function memoryMiB(kib) {
  return Number.isFinite(kib)?Math.round(kib/1024)+' MiB':'Not sampled';
}
function memoryRetainedHint(memory={}) {
  const count=Number.isFinite(memory.report_count)?memory.report_count:0;
  if(!count&&!memory.latest_incident?.timestamp_utc)return '';
  const countText=count+' retained report'+(count===1?'':'s');
  const when=new Date(memory.latest_incident?.timestamp_utc||'');
  return countText+(Number.isNaN(when.getTime())?'':' · Last incident '+when.toLocaleString());
}
function memoryHealthCard(memory={}) {
  const thresholds=memory.thresholds_kib||{},state=String(memory.state||'disabled'),retained=memoryRetainedHint(memory);
  let hint=retained?'Monitoring is disabled. '+retained:'Monitoring is disabled.';
  if(state==='armed')hint='Warning at '+memoryMiB(thresholds.warning);
  else if(state==='warned')hint='Tracing starts at '+memoryMiB(thresholds.tracing);
  else if(state==='tracing')hint='Capture at '+memoryMiB(thresholds.capture);
  else if(state==='captured')hint=retained||'0 retained reports';
  return healthCard('memory','Memory',el('span',{class:'badge'},memoryStateLabel(state)),el('p',{},'RSS: '+memoryMiB(memory.rss_kib)),el('p',{class:'muted'},hint));
}
function contextTokens(value) {
  return Number.isFinite(value)?Math.round(value).toLocaleString()+' tokens':'Not recorded';
}
function contextInputUsage(context={}) {
  const budget=Number.isFinite(context.budget_tokens)?Math.round(context.budget_tokens):null;
  const final=Number.isFinite(context.final_tokens)?Math.round(context.final_tokens):null;
  if(final===null)return 'Not assembled yet / '+(budget===null?'unknown':budget.toLocaleString())+' input tokens';
  let percent=Number.isFinite(context.usage_percent)?Math.round(context.usage_percent):null;
  if(percent===null&&budget>0)percent=Math.round(final*100/budget);
  return final.toLocaleString()+' / '+(budget===null?'unknown':budget.toLocaleString())+' input tokens'+(percent===null?'':' ('+percent+'%)');
}
function contextHealthCard(context={}) {
  const window=contextTokens(context.window_tokens);
  const trimmed=Array.isArray(context.trimmed_components)?context.trimmed_components.join(', '):['memory','rag','npc','summary'].filter(key=>context[key+'_trimmed']).join(', ');
  const compacted=context.compacted===true||Number(context.dropped_history||0)>0||Boolean(trimmed);
  return healthCard('models','Context',
    el('span',{class:'badge'},String(context.source||'global-fallback').replaceAll('-',' ')),
    el('p',{},contextInputUsage(context)),
    el('p',{class:'muted'},'Window '+window+' · Output reserve '+contextTokens(context.output_reserve_tokens)+' · Safety '+contextTokens(context.safety_margin_tokens)),
    el('p',{class:'muted'},(compacted?'Compacted':'Not compacted')+' · Dropped history '+(context.dropped_history||0)+' · Trimmed '+(trimmed||'none')));
}
function details(data) {
  return el('div',{class:'grid'},healthCard('system','Bridge',el('p',{},'Running: v'+data.deployment.version),el('p',{class:'muted'},'Revision: '+(data.deployment.commit.slice(0,12)||'unverified')),el('p',{},'Uptime: '+Math.floor(data.uptime_seconds/60)+' minutes'),el('p',{class:'muted'},'Installed files: v'+data.installed_version)),
    healthCard('telegram','Telegram',el('span',{class:'badge'},data.telegram.state),el('p',{class:'muted'},data.telegram.last_success?'Last successful poll: '+new Date(data.telegram.last_success*1000).toLocaleTimeString():'No successful polling observation yet.')),
    healthCard('database','Database',el('p',{},'SQLite '+data.database.sqlite_version+' · '+data.database.state),el('p',{},data.database.sessions+' private sessions'),el('p',{},data.database.messages+' stored messages')),
    contextHealthCard(data.context_diagnostics||{}),
    memoryHealthCard(data.memory_diagnostics||{}));
}
function memoryBytesMiB(value) {
  return Number.isFinite(value)?Math.round(value/1048576)+' MiB':'Unavailable';
}
function safeAllocationSite(value) {
  const raw=String(value||'external:unknown');
  if(/^(bridge|tests|tools)\//.test(raw))return raw;
  if(raw.includes('/')||raw.includes('\\'))return 'external:'+raw.replaceAll('\\','/').split('/').pop();
  return raw;
}
function memoryInterpretation(report) {
  const rss=report?.rss_kib,traced=report?.traced_current_bytes;
  if(typeof rss!=='number'||!Number.isFinite(rss)||rss<=0||typeof traced!=='number'||!Number.isFinite(traced))return 'The report is mixed; inspect allocation sites and process-memory totals.';
  const ratio=traced/(rss*1024);
  if(ratio>=0.60)return 'Python-traced allocations are a significant share of process memory.';
  if(ratio<=0.35)return 'Native or otherwise untraced memory appears significant.';
  return 'The report is mixed; inspect allocation sites and process-memory totals.';
}
function memoryDiagnosticsCard(summary={},detail=null) {
  const view=card('Memory diagnostics',el('div',{class:'memory-diagnostics-summary'},
    el('span',{class:'badge'},memoryStateLabel(summary.state)),
    el('p',{},'Current RSS: '+memoryMiB(summary.rss_kib)),
    el('p',{class:'muted'},'Warning '+memoryMiB(summary.thresholds_kib?.warning)+' · Trace '+memoryMiB(summary.thresholds_kib?.tracing)+' · Capture '+memoryMiB(summary.thresholds_kib?.capture))));
  if(detail===null) {view.append(el('p',{class:'muted'},'Memory diagnostics unavailable'));return view;}
  const reports=Array.isArray(detail.reports)?detail.reports.slice(0,3):[];
  if(!reports.length) {view.append(empty('No memory incident reports.'));return view;}
  const list=el('div',{class:'memory-incident-list'});
  for(const report of reports) {
    const smaps=report.smaps_kib||{},topSites=Array.isArray(report.top_sites)?report.top_sites.slice(0,10):[];
    const incident=el('section',{class:'memory-incident'},
      el('div',{class:'row spaced'},el('strong',{},new Date(report.timestamp_utc).toLocaleString()),el('span',{class:'badge'},memoryMiB(report.rss_kib))),
      el('p',{class:'muted'},'PSS '+memoryMiB(smaps.Pss)+' · Private dirty '+memoryMiB(smaps.Private_Dirty)+' · Anonymous '+memoryMiB(smaps.Anonymous)),
      el('p',{},'Python traced: '+memoryBytesMiB(report.traced_current_bytes)+' current · '+memoryBytesMiB(report.traced_peak_bytes)+' peak'),
      el('p',{},Number.isFinite(report.thread_count)?report.thread_count+' threads':'Thread count unavailable'),
      el('p',{class:'info-note'},memoryInterpretation(report)));
    const allocations=el('details',{class:'memory-allocations'},el('summary',{},'Top Python allocations'));
    if(topSites.length) {
      const items=el('ul');
      for(const site of topSites)items.append(el('li',{},safeAllocationSite(site.file)+':'+site.line+' · '+memoryBytesMiB(site.size_bytes)));
      allocations.append(items);
    } else allocations.append(el('p',{class:'muted'},'No traced allocation sites recorded.'));
    incident.append(allocations);list.append(incident);
  }
  view.append(list);return view;
}
async function dashboard() {
  const data=await api('/status');
  const [sessions,personas,worlds,memory]=await Promise.all([
    api('/sessions').catch(()=>({sessions:[data.session]})),
    api('/personas').catch(()=>({personas:[],unavailable:true})),
    api('/worlds').catch(()=>({worlds:[],unavailable:true})),
    api('/memory').catch(()=>({mode:'unknown',unavailable:true})),
  ]);
  state.session=data.session;
  const session=data.session;
  const characterName=String(session.character_file||'Character').replace(/\.[^.]+$/,'');
  const activePersona=personas.personas.find(item=>item.id===session.persona_id);
  const activeWorlds=worlds.worlds.filter(item=>item.active).map(item=>item.filename.replace(/\.json$/i,''));
  const sessionPortrait=el('img',{class:'session-portrait',alt:characterName,decoding:'async',referrerpolicy:'no-referrer'});
  const sessionPortraitFrame=el('div',{class:'session-portrait-frame',hidden:true},sessionPortrait);
  const portraitSlot=el('div',{class:'session-portrait-slot','aria-hidden':'true'},icon('characters'),sessionPortraitFrame);
  const status=el('div',{class:'story-status'},
    storyStatus('personas','Persona',personas.unavailable?'Unavailable':activePersona?.name||'Off'),
    storyStatus('worlds','World',worlds.unavailable?'Unavailable':activeWorlds.length?activeWorlds.join(', '):'None'),
    storyStatus('memory','Memory',memory.unavailable?'Unavailable':memory.mode==='on'?'On':'Off'));
  const summary=el('div',{class:'session-summary'},portraitSlot,
    el('div',{class:'session-meta'},el('span',{class:'eyebrow'},'CONTINUE YOUR STORY'),el('h2',{},session.title),
      el('p',{class:'story-character'},'with '+characterName),el('span',{class:'badge'},session.model_id)));
  const back=button('Continue',()=>{const app=window.Telegram?.WebApp;if(typeof app?.close==='function')app.close();else notice('Continue in your Telegram bot chat.');});back.append(icon('arrow'));
  const sessionCard=el('section',{class:'card dashboard-session story-card'},summary,status,
    el('div',{class:'session-actions'},back,button('Sessions',()=>navigate('sessions'),'secondary')));
  if(session.character_file)api('/characters/'+encodeURIComponent(session.character_file)+'/portrait',{binary:true}).then(blob=>{
    if(!blob||blob.size===0)throw new Error('empty portrait');
    const url=URL.createObjectURL(blob);let revoked=false;const revoke=()=>{if(!revoked){revoked=true;URL.revokeObjectURL(url);}};
    sessionPortrait.onload=()=>{sessionPortraitFrame.hidden=false;revoke();};sessionPortrait.onerror=()=>{revoke();sessionPortraitFrame.hidden=true;};sessionPortrait.src=url;
  }).catch(()=>{sessionPortraitFrame.hidden=true;});

  const recentList=el('div',{class:'recent-story-list'});
  for(const item of sessions.sessions.slice(0,3)) {
    const active=item.session_id===session.session_id;
    const row=button('',()=>navigate('sessions'),'recent-story');
    row.append(icon('sessions'),el('span',{class:'recent-story-copy'},el('strong',{},item.title),el('small',{},item.character_file+' · '+item.model_id)),active?el('span',{class:'badge'},'Active'):icon('chevron'));
    recentList.append(row);
  }
  if(!sessions.sessions.length)recentList.append(empty('Create a session to start a story.'));
  const recent=el('section',{class:'recent-stories'},el('div',{class:'section-header'},el('h2',{},'Recent stories'),button('View all',()=>navigate('sessions'),'secondary')),recentList);

  const shortcuts=el('div',{class:'dashboard-shortcuts'});
  for(const [key,label,hint] of [['characters','Characters','Find your next story'],['models','Models','Choose story and utility models'],['memory','Memory','Keep important details'],['worlds','Worlds','Shape lore and context']]) {
    const action=button(label,()=>navigate(key),'shortcut');action.prepend(icon(key));action.append(el('small',{},hint),icon('chevron'));shortcuts.append(action);
  }
  const health=el('details',{class:'card dashboard-health'},el('summary',{},'Bridge health'),details(data));health.querySelector('.grid').classList.add('health-grid');
  return el('div',{class:'dashboard-layout'},sessionCard,recent,
    el('div',{class:'section-header dashboard-quick-heading'},el('h2',{},'Quick actions')),shortcuts,health);
}

function storyStatus(iconName,label,value) {
  return el('div',{class:'story-status-item'},icon(iconName),el('span',{},el('small',{},label),el('strong',{},value)));
}

async function renderSystem() {
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const [data,operations,memoryDetail]=await Promise.all([api('/status'),api('/jobs'),api('/memory-diagnostics').catch(()=>null)]);scope.set(data.session);
    const history=card('Operations',el('p',{class:'muted'},'Results are private to your account. Interrupted jobs are not silently replayed. Refresh this page to check pending work.'));
    for(const job of operations.jobs) {
      const view=card(job.kind.replaceAll('_',' '),el('span',{class:'badge'},job.state),el('p',{class:'muted'},new Date(job.created_at*1000).toLocaleString()));
      if(job.error)view.append(el('p',{},job.error));
      if(job.state==='succeeded')view.append(el('details',{},el('summary',{},'Result'),el('pre',{},JSON.stringify(job.result,null,2))));
      if(job.state==='succeeded'&&job.kind==='character_optimize'&&job.result?.nonce) {
        view.append(button('Review saved preview',async()=>{
          const {reopenCharacterPreview}=await import('./characters.js');
          await reopenCharacterPreview(job.result);
        }));
      }
      history.append(view);
    }
    if(!operations.jobs.length)history.append(empty('No recent operations.'));
    root.replaceChildren(card('System',el('p',{class:'muted'},'Runtime status is observed, not inferred from installed files.'),button('Refresh status',load,'secondary')),details(data),
      memoryDiagnosticsCard(memoryDetail?.summary||data.memory_diagnostics||{},memoryDetail),
      card('Verified update',el('p',{},data.automatic_update_trust_configured?'A public release trust file is configured. The updater still verifies signatures and deployment safety.':'Automatic update trust is not configured. Add the independently obtained public release key and external allowed-signers path in .env, then rerun install.sh.'),button('Review latest release',review)),history);
  }
  async function review() {
    const release=await api('/update');
    const page=card('Review release',el('p',{},'Installed: v'+release.installed+' · Latest: v'+release.latest));
    if(!release.update_available) {
      const message=release.status==='already_latest'?'Already latest. No update is required.':'The installed version is newer than the latest published release. No update is available.';
      page.append(el('p',{class:'muted'},message),el('div',{class:'actions'},button('Back',load,'secondary')));
      root.replaceChildren(page);return;
    }
    page.append(el('pre',{},release.notes),el('p',{class:'muted'},'Confirmation expires in five minutes. Signature, source cleanliness, dependency and deployment guards remain active.'));
    page.append(el('div',{class:'actions'},button('Back',load,'secondary'),button('Install reviewed release',async()=>{
      if(!await confirmAction('Install v'+release.latest+' and restart the bridge? Readiness will be confirmed only by the new process after polling resumes.'))return;
      await updateAndWait(release);
    })));
    root.replaceChildren(page);
  }
  async function updateAndWait(release) {
    const old=await api('/status');
    const operationId=crypto.randomUUID();
    const status=el('p',{},'Submitting verified update…');
    root.replaceChildren(card('Updating bridge',status,el('progress',{}),button('Return to System',load,'secondary')));
    let job,expectedCommit='',scheduled=false,requestAccepted=false;
    try {
      job=await api('/update',{method:'POST',body:sessionBody({version:release.latest,confirmation:release.confirmation,confirm:true,operation_id:operationId})});
      requestAccepted=true;
    } catch(error) {status.textContent='Update submission was not confirmed. Check System → Operations before retrying; no automatic resubmission was made.';throw error;}
    const deadline=Date.now()+240000;
    while(Date.now()<deadline) {
      await new Promise(resolve=>setTimeout(resolve,1500));
      try {
        job=await api('/jobs/'+encodeURIComponent(job.id));
        if(job.state==='failed')throw new Error(job.error||'Update failed.');
        if(job.state==='succeeded') {
          if(job.result.status==='already_latest') {status.textContent=job.result.message;return;}
          scheduled=job.result.status==='restart_scheduled';expectedCommit=job.result.commit||'';
        }
        const current=await api('/status');
        const changed=current.started_at>old.started_at&&current.deployment.version===release.latest&&current.telegram.state==='polling';
        if(changed&&expectedCommit&&current.deployment.commit===expectedCommit) {status.textContent='Update complete. Running v'+release.latest+'; Telegram polling has resumed.';notice('Verified update complete.');return;}
        if(job.state==='interrupted'&&!scheduled) {status.textContent='The update operation was interrupted. A restart may have happened; verify the running revision in System or the Telegram completion notification.';return;}
        status.textContent=scheduled?'Release installed; waiting for the replacement process and Telegram polling.':'Update '+job.state+'. Preparing and verifying the release.';
      } catch(error) {
        if(error.status===401||error.status===403)throw error;
        if(job?.state==='failed') {status.textContent=job.error||'Update failed.';throw error;}
        status.textContent=requestAccepted?'Connection interrupted during update. Reconnecting; success is not yet confirmed.':error.message;
      }
    }
    status.textContent='Restart readiness was not confirmed within the observation window. Inspect System → Operations and the user-service journal; do not assume the update failed or succeeded.';
  }
  await load();return root;
}
registerPage('dashboard','Home',dashboard);
registerPage('system','System',renderSystem);
