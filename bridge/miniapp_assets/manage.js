import {api,state,navigate,openChat,registerPage} from './app.js';
import {el,card,button,notice,sessionContext} from './ui.js';
import {icon} from './icons.js';

const toolSections=[
  ['Story controls',[
    ['director','Director Room','Private plans, decisions and scene direction','director'],
    ['trackers','Story trackers','Read-only relationships, goals and saved checks','trackers'],
    ['check','Check','Resolve an action in Telegram','check'],
    ['imagine','Imagine','Create a scene image in Telegram','imagine'],
  ]],
  ['Story setup',[
    ['personas','Persona','Choose who you are in the story','personas'],
    ['worlds','Worlds','Shape lore, setting and context','worlds'],
    ['sessions','All sessions','Switch or manage your saved stories','sessions'],
  ]],
  ['Knowledge',[
    ['memory','Memory','Keep important details across replies','memory'],
    ['npcs','NPC Bank','Review supporting-character state','characters'],
    ['databank','Data Bank','Ground replies in your documents','databank'],
  ]],
];
const settingsSections=[
  ['Models and generation',[
    ['models','Model roles','Story, Utility and Director selections','models'],
    ['models','Generation','Creativity, reply length, reasoning and presets','generation','generation'],
  ]],
  ['Bridge and activity',[
    ['usage','Usage','Review provider-reported token activity','usage'],
    ['system','System & operations','Connection status, job results and verified updates','system'],
  ]],
];

function manageLink(page,label,description,iconName,dataKey=page) {
  const control=button('',()=>navigate(page),'manage-link');control.dataset.page=dataKey;
  control.setAttribute('aria-label',label+': '+description);
  control.append(icon(iconName),el('span',{class:'manage-link-copy'},el('strong',{},label),el('small',{},description)),icon('chevron'));
  return control;
}
function hub(sections) {
  return el('div',{class:'manage-page'},...sections.map(([title,items])=>
    el('section',{class:'manage-section'},el('h2',{class:'manage-section-title'},title),
      el('div',{class:'manage-list'},...items.map(item=>manageLink(...item))))));
}

async function handoff(key) {
  const {session}=await api('/status');state.session=session;
  const isCheck=key==='check',command='/'+key,title=isCheck?'Check':'Imagine';
  const copy=button('Copy command',async()=>{
    if(!navigator.clipboard?.writeText){notice('Select and copy '+command+', then send it in your Telegram bot chat.');return;}
    try {await navigator.clipboard.writeText(command);notice(command+' copied. Send it in your Telegram bot chat.');}
    catch {notice('Select and copy '+command+', then send it in your Telegram bot chat.');}
  },'secondary');
  const instructions=card('Continue in Telegram',el('span',{class:'badge'},'Telegram panel'),
    el('p',{},isCheck?'Resolve an action with Auto, Director or a manual check.':'Generate from the current scene or write a custom image prompt.'),
    el('ol',{class:'handoff-steps'},el('li',{},'Copy the command below.'),el('li',{},'Open chat and send it to the bot.'),el('li',{},'Choose your options in the '+title+' panel.')),
    el('code',{class:'handoff-command',tabindex:'0'},command),
    el('div',{class:'actions'},button('Open chat',openChat),copy),
    el('p',{class:'muted'},'Open chat closes the MiniApp. You still need to send the command.'));
  const choices=isCheck?[
    ['Auto · Utility','Let the Utility model choose the check details.'],
    ['Director','Use the Director model for the check.'],
    ['Manual','Set the domain, DC and action yourself.'],
  ]:[
    ['Current Scene','Create an image from the active story.'],
    ['Custom Prompt','Describe the image you want.'],
    ['Options','Choose Realism or Anime, the image model and the image size.'],
  ];
  return el('div',{class:'handoff-page'},sessionContext(session),instructions,
    card('In the '+title+' panel',...choices.map(([label,description])=>el('div',{class:'handoff-option'},el('h3',{},label),el('p',{class:'muted'},description)))),
    isCheck?button('View saved checks',()=>navigate('trackers'),'secondary'):null);
}

registerPage('tools','Tools',()=>hub(toolSections));
registerPage('manage','Tools',()=>hub(toolSections));
registerPage('settings','Settings',()=>hub(settingsSections));
registerPage('advanced','Advanced settings',()=>hub([settingsSections[1]]));
registerPage('check','Check',()=>handoff('check'));
registerPage('imagine','Imagine',()=>handoff('imagine'));
