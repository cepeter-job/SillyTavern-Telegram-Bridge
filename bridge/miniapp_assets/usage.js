import {api,registerPage} from './app.js';
import {el,card,button,empty} from './ui.js';
import {icon} from './icons.js';
const format=new Intl.NumberFormat(undefined,{maximumFractionDigits:0});
const compact=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1});
export const tokenNumber=value=>value==null?'—':format.format(value);
const purposes={summary:'Continuity summaries',memory:'Memory curation',scene:'Scene state',story:'Story replies',generation:'Regeneration & continuation',choices:'Light Novel choices',language:'Language rendering',humanizer:'Humanizer',image:'Image replies',edit:'Edited replies',rank:'Character ranking',optimizer:'Character optimizer'};
function stat(label,value,hint,kind='') {
  return el('div',{class:'usage-stat '+kind},el('span',{class:'stat-label'},label),el('strong',{class:'stat-value',title:tokenNumber(value),'aria-label':label+': '+tokenNumber(value)},value==null?'—':value>=10000?compact.format(value):tokenNumber(value)),el('small',{},hint));
}
export function usageOverview(data,{compact=false}={}) {
  const t=data.totals;
  const hasCalls=t.calls>0;
  const grid=el('div',{class:'usage-stats'},stat('Reported tokens',t.total_tokens,hasCalls?'Known tokens, not a bill':'Tracking starts with new requests','primary-stat'),
    stat('Input',t.input_tokens,'Prompt + context'),stat('Output',t.output_tokens,'Provider output, including reasoning'));
  const coverage=hasCalls?`${t.complete_calls} of ${t.calls} calls fully reported`:'No recorded usage';
  const block=el('section',{class:'usage-overview'},grid,el('div',{class:'usage-coverage'},icon('shield'),el('span',{},coverage)));
  if(!compact)block.append(el('div',{class:'usage-substats'},
    stat('Cached input',t.cached_tokens,'Part of input, not extra'),stat('Reasoning',t.reasoning_tokens,'Part of output, not extra'),
    stat('Failed calls',t.failed_calls,'Reported usage is still counted')));
  if(!compact) {
    const exact=el('dl',{class:'exact-totals'});
    for(const [name,key] of [['Total','total_tokens'],['Input','input_tokens'],['Output','output_tokens'],['Cached input','cached_tokens'],['Reasoning','reasoning_tokens']])exact.append(el('dt',{},name),el('dd',{},tokenNumber(t[key])));
    block.append(el('details',{class:'usage-exact'},el('summary',{},'Exact token counts'),exact));
  }
  if(!hasCalls&&!compact)block.append(empty('No recorded usage in this period. Send a message in Telegram after installing this version to begin tracking.'));
  else if(t.complete_calls<t.calls)block.append(el('p',{class:'info-note'},'Some calls did not return complete usage. Totals show only known tokens; — means unavailable, not zero.'));
  return block;
}
function breakdown(title,items,key) {
  const list=el('div',{class:'usage-breakdown'});
  for(const item of items)list.append(el('div',{class:'usage-row'},el('div',{},el('strong',{},key==='purpose'?(purposes[item[key]]||item[key]):item[key]),el('small',{},`${item.calls} calls · ${item.complete_calls} fully reported`)),el('strong',{class:'tabular'},tokenNumber(item.total_tokens))));
  if(!items.length)list.append(empty('Activity will appear here.'));
  return card(title,list);
}
function trend(data) {
  const days=Number(data.period.slice(0,-1));
  const last=new Date(data.as_of*1000);last.setUTCHours(0,0,0,0);
  const rows=[];
  for(let i=days;i>=0;i--){const day=new Date(last.getTime()-i*86400000).toISOString().slice(0,10);rows.push(data.daily.find(row=>row.day===day)||{day,total_tokens:null,calls:0});}
  const max=Math.max(1,...rows.map(row=>row.total_tokens||0));
  const bars=el('div',{class:'usage-chart',role:'img','aria-label':'Daily provider-reported tokens in UTC. Exact figures are in the daily table below.'});
  for(const row of rows){const bar=el('div',{class:'usage-bar','data-empty':row.total_tokens==null,title:row.day+': '+tokenNumber(row.total_tokens)});bar.style.setProperty('--bar-height',(row.total_tokens==null?2:Math.max(0,row.total_tokens/max*100))+'%');bars.append(bar);}
  const table=el('table',{class:'usage-table'},el('caption',{},'Daily usage · UTC · partial first day'),el('thead',{},el('tr',{},el('th',{scope:'col'},'Date'),el('th',{scope:'col'},'Tokens'),el('th',{scope:'col'},'Calls'))),
    el('tbody',{},rows.map(row=>el('tr',{},el('th',{scope:'row'},row.day),el('td',{},tokenNumber(row.total_tokens)),el('td',{},row.calls)))));
  return card('Daily activity',bars,el('div',{class:'chart-range'},el('span',{},rows[0].day),el('span',{},rows.at(-1).day)),el('details',{},el('summary',{},'View daily figures'),el('div',{class:'table-scroll'},table)));
}
async function renderUsage() {
  const period=el('select',{'aria-label':'Usage period'},...['1d','7d','30d'].map((value,index)=>el('option',{value,selected:value==='7d'},['Last 24 hours','Last 7 days','Last 30 days'][index])));
  const scope=el('select',{'aria-label':'Usage scope'},el('option',{value:'session'},'Current session'),el('option',{value:'all'},'All my sessions'));
  const body=el('div'),root=el('div',{class:'usage-dashboard'});let sequence=0;
  async function load() {
    const request=++sequence;body.setAttribute('aria-busy','true');
    try {
      const data=await api('/usage?period='+period.value+'&scope='+scope.value);
      if(request!==sequence)return;
      body.replaceChildren(usageOverview(data),trend(data),el('div',{class:'grid'},breakdown('By model · top 12',data.models,'model'),breakdown('By task',data.purposes,'purpose')),
        el('details',{class:'card usage-method'},el('summary',{},'How this is measured'),el('p',{},'Actual provider-reported tokens only. New story, choice, rewrite, image, continuity and character-utility requests are recorded after this version is installed. Unscoped administrative work and embeddings are not included. Group and forum-chat usage is not aggregated into this private-chat view.'),
          el('p',{},'Input includes cached input. Output includes reasoning. These subsets are never added twice. Provider billing, subscription quotas and historical usage are not reconstructed. A failed request may still consume tokens.'),
          el('p',{},'Records belong to your private Telegram chat. Deleting a session deletes its usage. Entries older than 90 days are pruned when another tracked request completes. Charts use UTC. Refresh to see new activity.')));
    }catch(error){if(request===sequence)body.replaceChildren(card('Usage is unavailable',el('p',{},error.message),button('Try again',load,'secondary')));}
    finally{if(request===sequence)body.removeAttribute('aria-busy');}
  }
  period.addEventListener('change',load);scope.addEventListener('change',load);
  root.append(el('div',{class:'usage-toolbar'},period,scope,button('Refresh',load,'secondary')),body);
  await load();return root;
}
registerPage('usage','Usage',renderUsage);
