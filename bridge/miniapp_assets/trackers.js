import {api,navigate,registerPage} from './app.js';
import {el,card,button,empty,sessionContext} from './ui.js';

const label=value=>String(value??'').replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());
const signed=value=>value>=0?'+'+value:String(value);
const badge=value=>el('span',{class:'badge'},value);
const detail=(name,value)=>value?el('p',{},el('strong',{},name+': '),value):null;
const entry=(name,...children)=>el('article',{class:'card tracker-entry'},el('h3',{},name),...children);

function section(key,title,items,render,description='') {
  return el('section',{class:'tracker-section','aria-labelledby':'tracker-'+key},
    el('div',{class:'section-header'},el('h2',{id:'tracker-'+key,tabindex:'-1'},title),badge(items.length+' saved')),
    description?el('p',{class:'muted'},description):null,
    items.length?el('div',{class:'grid'},...items.map(render)):empty('No saved '+title.toLowerCase()+'.'));
}

function relationship(item) {
  return entry(item.name,
    el('p',{class:'row'},badge('BOND '+item.bond),badge(label(item.tier))),
    el('p',{},'Sparks '+item.sparks+' · Grudge '+item.grudge));
}

function agenda(item) {
  return entry(item.name,el('p',{},item.objective),
    el('p',{class:'row'},badge(label(item.status)),badge('Step '+item.step+' of '+item.max_steps)),
    detail('Location',item.location));
}

function modifier(item) {
  return entry(item.name,el('p',{class:'row'},badge(label(item.domain)),badge('Modifier: '+signed(item.modifier))));
}

function faction(item) {
  return entry(item.name,detail('Goal',item.goal),detail('Morale',item.morale),detail('Conflict',item.conflict));
}

function quest(item) {
  const savedStatus=String(item.status||'');
  const status=item.narrative_linked
    ? 'Narrative: '+(savedStatus.startsWith('native=')?savedStatus.slice(7).split('/').map(label).join(' / '):'Unavailable')
    : label(savedStatus);
  return entry(label(item.name.replaceAll('-',' ')),el('p',{},item.objective),
    el('p',{class:'row'},badge(label(item.kind)),badge(status)),
    el('p',{},'Progress: '+item.progress_current+(item.progress_target>0?' of '+item.progress_target:'')),
    detail('Reward',item.reward));
}

function task(item) {
  return entry(label(item.name.replaceAll('-',' ')),detail('Objective',item.objective),
    el('p',{class:'row'},badge(label(item.status)),badge(item.progress_current+' / '+item.progress_target)),
    detail('Stage',item.stage),detail('Completed',(item.completed_steps||[]).join(' · ')),
    detail('Pending',(item.pending_steps||[]).join(' · ')),
    detail('Complications',(item.complications||[]).join(' · ')),detail('Established consequence',item.consequence));
}

function check(item) {
  return entry(item.action,
    el('p',{class:'muted'},(item.actor==='user'?'You':item.actor)+' · '+label(item.domain)),
    el('p',{class:'row'},badge(label(item.outcome)),badge('Margin: '+signed(item.delta))),
    el('p',{},'Roll: '+item.roll+' · Modifier: '+signed(item.modifier)+' · DC: '+item.dc));
}

function freshness(data) {
  const updated=new Date(Number(data.last_updated_at)*1000);
  return el('p',{class:'muted'},'Last updated: ',
    data.last_updated_at!=null&&Number.isFinite(updated.getTime())
      ? el('time',{datetime:updated.toISOString()},updated.toLocaleString())
      : 'Not recorded yet');
}

async function renderTrackers() {
  const data=await api('/trackers');
  const intro=card('Saved story state',
    badge('Read only'),
    el('p',{class:'muted'},'Known story facts only. Director plans are separate from these saved details.'),
    el('div',{class:'row spaced'},freshness(data),button('Refresh',()=>navigate('trackers'),'secondary')),
    el('p',{class:'muted'},'Use /trackers in Telegram for a compact view of these saved details.'));
  intro.classList.add('tracker-overview');
  if(data.pending)intro.append(el('p',{role:'status'},badge('Catching up'),
    ' Newer story replies are waiting for tracker updates. Refresh later to check again.'));
  const root=el('div',{class:'story-trackers-page'},data.session?sessionContext(data.session):null,intro);
  if(!data.session) {
    root.append(card('No story selected',empty('Open or create a story in Sessions to see its saved trackers.'),
      button('Sessions',()=>navigate('sessions'),'secondary')));
    return root;
  }
  const sections=[
    ['relationships','Relationships',relationship],
    ['agendas','Visible agendas',agenda],
    ['inventory','Inventory',modifier],
    ['skills','Skills',modifier],
    ['conditions','Conditions',modifier],
    ['factions','Factions',faction],
    ['quests','Quests',quest,'Linked quests follow the story’s narrative status.'],
    ['tasks','Tasks',task,'Progress comes from the saved story, not the dice alone.'],
    ['checks','Recent checks',check,'Recorded rolls and their outcomes.'],
  ];
  if(!sections.some(([key])=>(data[key]||[]).length)) {
    root.append(card('No saved trackers yet',empty('Details will appear here as your story progresses.')));
    return root;
  }
  root.append(el('nav',{class:'tracker-navigation','aria-label':'Saved tracker categories'},
    ...sections.map(([key,title])=>el('a',{href:'#tracker-'+key},title,el('span',{class:'badge'},(data[key]||[]).length)))));
  for(const [key,title,render,description] of sections)root.append(section(key,title,data[key]||[],render,description));
  return root;
}

registerPage('trackers','Story trackers',renderTrackers);
