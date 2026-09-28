import {api,state,createSessionScope,registerPage,navigate} from './app.js';
import {el,card,button,empty,confirmAction,notice} from './ui.js';
import {icon} from './icons.js';
import {usageOverview} from './usage.js';
function details(data) {
  return el('div',{class:'grid'},card('Bridge',el('p',{},'Running: v'+data.deployment.version),el('p',{class:'muted'},'Revision: '+(data.deployment.commit.slice(0,12)||'unverified')),el('p',{},'Uptime: '+Math.floor(data.uptime_seconds/60)+' minutes'),el('p',{class:'muted'},'Installed files: v'+data.installed_version)),
    card('Telegram',el('span',{class:'badge'},data.telegram.state),el('p',{class:'muted'},data.telegram.last_success?'Last successful poll: '+new Date(data.telegram.last_success*1000).toLocaleTimeString():'No successful polling observation yet.')),
    card('Database',el('p',{},'SQLite '+data.database.sqlite_version+' · '+data.database.state),el('p',{},data.database.sessions+' private sessions'),el('p',{},data.database.messages+' stored messages')));
}
async function dashboard() {
  const [data,operations]=await Promise.all([api('/status'),api('/jobs')]);state.session=data.session;
  const session=data.session;
  const sessionPortrait=el('img',{class:'session-portrait',alt:session.character_file,decoding:'async',referrerpolicy:'no-referrer'});
  const sessionPortraitFrame=el('div',{class:'session-portrait-frame',hidden:true},sessionPortrait);
  const portraitSlot=el('div',{class:'session-portrait-slot','aria-hidden':'true'},icon('characters'),sessionPortraitFrame);
  const summary=el('div',{class:'session-summary'},portraitSlot,el('div',{class:'session-meta'},el('span',{class:'eyebrow'},'CURRENT SESSION'),el('h3',{},session.title),el('p',{class:'muted session-model'},session.character_file),el('span',{class:'badge'},session.model_id)));
  const back=button('Back to chat',()=>{const app=window.Telegram?.WebApp;if(typeof app?.close==='function')app.close();else notice('Continue in your Telegram bot chat.');});back.append(icon('arrow'));
  const sessionCard=el('section',{class:'card dashboard-session dashboard-hero'},summary,el('div',{class:'session-actions'},back,button('Switch session',()=>navigate('sessions'),'secondary')));
  api('/characters/'+encodeURIComponent(session.character_file)+'/portrait',{binary:true}).then(blob=>{
    if(!blob||blob.size===0)throw new Error('empty portrait');
    const url=URL.createObjectURL(blob);let revoked=false;const revoke=()=>{if(!revoked){revoked=true;URL.revokeObjectURL(url);}};
    sessionPortrait.onload=()=>{sessionPortraitFrame.hidden=false;revoke();};sessionPortrait.onerror=()=>{revoke();sessionPortraitFrame.hidden=true;};sessionPortrait.src=url;
  }).catch(()=>{sessionPortraitFrame.hidden=true;});
  const shortcuts=el('div',{class:'dashboard-shortcuts'});
  for(const [key,label,hint] of [['characters','Characters','Find your next story'],['models','Models','Tune your generation'],['memory','Memory','Keep the important details'],['worlds','Worlds','Set the scene']]) {
    const action=button(label,()=>navigate(key),'shortcut');action.prepend(icon(key));action.append(el('small',{},hint));shortcuts.append(action);
  }
  const usageBody=el('div',{class:'usage-home-body'},el('p',{class:'muted'},'Loading reported usage…'));
  const usage=el('section',{class:'card dashboard-usage'},el('div',{class:'section-header'},el('div',{},el('span',{class:'eyebrow'},'CURRENT SESSION · 7 DAYS'),el('h2',{},'Every token, in view.')),button('View usage',()=>navigate('usage'),'secondary')),usageBody);
  api('/usage?period=7d&scope=session').then(result=>{
    if(result.session_id!==session.session_id){usageBody.replaceChildren(el('p',{class:'muted'},'The active session changed. Refresh to see its usage.'));return;}
    usageBody.replaceChildren(usageOverview(result,{compact:true}));
  }).catch(()=>usageBody.replaceChildren(el('p',{class:'muted'},'Usage is temporarily unavailable. Open Usage to retry.')));
  const latest=operations.jobs?.[0];
  const recent=card('Recent activity',latest?el('div',{class:'row spaced'},el('div',{},el('strong',{},latest.kind.replaceAll('_',' ')),el('p',{class:'muted'},new Date(latest.created_at*1000).toLocaleString())),el('span',{class:'badge'},latest.state)):el('p',{class:'muted'},'Nothing running. Start a conversation or manage your characters.'));
  recent.classList.add('dashboard-recent');recent.append(button('All operations',()=>navigate('system'),'secondary'));
  const health=el('details',{class:'card dashboard-health'},el('summary',{},'Bridge health'),details(data));health.querySelector('.grid').classList.add('health-grid');
  return el('div',{class:'dashboard-layout'},sessionCard,el('div',{class:'section-header'},el('h2',{},'Make it yours'),el('span',{class:'eyebrow'},'QUICK CONTROLS')),shortcuts,usage,recent,health);
}

async function renderSystem() {
  const root=el('div');
  const scope=createSessionScope(),sessionBody=scope.body;
  async function load() {
    const [data,operations]=await Promise.all([api('/status'),api('/jobs')]);scope.set(data.session);
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
      card('Verified update',el('p',{},data.automatic_update_trust_configured?'A public release trust file is configured. The updater still verifies signatures and deployment safety.':'Automatic update trust is not configured. Add the independently obtained public release key and external allowed-signers path in .env, then rerun install.sh.'),button('Review latest release',review)),history);
  }
  async function review() {
    const release=await api('/update');
    const page=card('Review release',el('p',{},'Installed: v'+release.installed+' · Latest: v'+release.latest),el('pre',{},release.notes),el('p',{class:'muted'},'Confirmation expires in five minutes. Signature, source cleanliness, dependency and deployment guards remain active.'));
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
