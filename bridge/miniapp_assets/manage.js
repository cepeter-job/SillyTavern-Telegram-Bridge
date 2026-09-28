import {navigate,registerPage} from './app.js';
import {el,button} from './ui.js';
import {icon} from './icons.js';

const sections = [
  ['Story setup', [
    ['models','Models','Choose story and utility models','models'],
    ['personas','Persona','Define who you are in the story','personas'],
    ['worlds','Worlds','Shape lore, setting and context','worlds'],
    ['models','Generation','Tune creativity, length and reasoning','generation','generation'],
  ]],
  ['Knowledge', [
    ['memory','Memory','Keep important details across replies','memory'],
    ['databank','Data Bank','Ground replies in your documents','databank'],
  ]],
  ['Advanced', [
    ['advanced','Advanced settings','Fine-tune your workspace','advanced'],
  ]],
];

function manageLink(page,label,description,iconName,dataKey=page) {
  const control=button('',()=>navigate(page),'manage-link');
  control.dataset.page=dataKey;
  control.setAttribute('aria-label',label+': '+description);
  control.append(icon(iconName),el('span',{class:'manage-link-copy'},el('strong',{},label),el('small',{},description)),icon('chevron'));
  return control;
}

function renderManage() {
  const root=el('div',{class:'manage-page'});
  for(const [title,items] of sections) {
    root.append(el('section',{class:'manage-section'},el('h2',{class:'manage-section-title'},title),el('div',{class:'manage-list'},...items.map(item=>manageLink(...item)))));
  }
  return root;
}

function renderAdvanced() {
  return el('div',{class:'manage-page'},
    el('section',{class:'manage-section'},el('h2',{class:'manage-section-title'},'Insights'),
      el('div',{class:'manage-list'},manageLink('usage','Usage','Review provider-reported token activity','usage'))));
}

registerPage('manage','Manage',renderManage);
registerPage('advanced','Advanced settings',renderAdvanced);
