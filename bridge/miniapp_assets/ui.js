export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (key === 'value') node.value = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
export function notice(message,kind='status') {
  const target=document.getElementById('notice');
  clearTimeout(notice.timer);
  target.replaceChildren(el('span',{},message));target.hidden=false;target.dataset.kind=kind;
  target.setAttribute('role',kind==='error'?'alert':'status');target.setAttribute('aria-live',kind==='error'?'assertive':'polite');
  const dismiss=el('button',{type:'button',class:'notice-dismiss','aria-label':'Dismiss notification',onclick:()=>{target.hidden=true;}},'Dismiss');
  if(kind==='error')target.append(dismiss);
  if(kind!=='error')notice.timer=setTimeout(()=>{target.hidden=true;},7000);
}
// Associate modals opened before an action's first await with that exact opener.
let initiatingControl=null;
const closedDialogs=new WeakSet();
export function button(label, action, kind = '') {
  return el('button', {type:'button', class:kind, onclick:async event => {
    const target = event.currentTarget;
    if(target.disabled||target.getAttribute('aria-busy')==='true'||target.getAttribute('aria-disabled')==='true')return;
    const opener={control:target,focused:document.activeElement===target};
    target.disabled = true;target.setAttribute('aria-busy','true');
    try {
      const previous=initiatingControl;let result;
      try {initiatingControl=opener;result=action();}finally{initiatingControl=previous;}
      await result;
    } catch (error) {
      const recovery=error.status===409?' Refresh this page and review the current story before trying again.':'';
      notice((error.message || 'Operation failed.')+recovery,'error');
    }
    finally {
      target.disabled = target.getAttribute('aria-disabled')==='true';target.removeAttribute('aria-busy');
      if(closedDialogs.delete(target)&&target.isConnected&&!target.disabled&&document.activeElement===document.body) {
        target.focus({preventScroll:true});
      }
    }
  }}, label);
}
export function sessionContext(session) {
  const character=String(session?.character_file||'').replace(/\.[^.]+$/,'');
  const meta=[character,session?.mode?String(session.mode).replaceAll('_',' '):''].filter(Boolean).join(' · ');
  return el('section',{class:'session-context','aria-label':'Current story'},
    el('span',{class:'eyebrow'},'CURRENT STORY'),el('strong',{},session?.title||'No story selected'),
    meta?el('span',{class:'muted'},meta):null);
}
export function feedback(message,kind='error') {
  return el('div',{class:'feedback feedback-'+kind,role:kind==='error'?'alert':'status'},el('p',{},message));
}
export function field(label, input) {
  const id='field-'+crypto.randomUUID();input.id=id;
  if(input.type==='checkbox')return el('div',{},el('label',{for:id},input,el('span',{},label)));
  return el('div',{},el('label',{for:id},label),input);
}
export function card(title, ...children) { return el('section', {class:'card'}, el('h2',{},title), ...children); }
export function empty(text = 'Nothing here yet.') { return el('p', {class:'empty'}, text); }
export function confirmAction(message) { return new Promise(resolve => {
  const opener=initiatingControl;
  const popup = el('dialog', {}, el('h2',{},'Confirm action'), el('p',{},message));
  const done = answer => {
    popup.close();popup.remove();
    if(opener?.focused)closedDialogs.add(opener.control);
    resolve(answer);
  };
  popup.append(el('div',{class:'actions'},button('Cancel',()=>done(false),'secondary'),button('Confirm',()=>done(true),'danger')));
  popup.addEventListener('cancel',event=>{event.preventDefault();done(false);}); document.body.append(popup); popup.showModal();
}); }
