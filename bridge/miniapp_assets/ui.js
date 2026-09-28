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
export function notice(message) {
  const target = document.getElementById('notice'); target.textContent = message; target.hidden = false;
  clearTimeout(notice.timer); notice.timer = setTimeout(() => { target.hidden = true; }, 7000);
}
export function button(label, action, kind = '') {
  return el('button', {type:'button', class:kind, onclick:async event => {
    const target = event.currentTarget; target.disabled = true;target.setAttribute('aria-busy','true');
    try { await action(); } catch (error) { notice(error.message || 'Operation failed.'); }
    finally { target.disabled = false;target.removeAttribute('aria-busy'); }
  }}, label);
}
export function field(label, input) { const id = 'field-' + crypto.randomUUID(); input.id = id; return el('div', {}, el('label', {for:id}, label), input); }
export function card(title, ...children) { return el('section', {class:'card'}, el('h2',{},title), ...children); }
export function empty(text = 'Nothing here yet.') { return el('p', {class:'empty'}, text); }
export function confirmAction(message) { return new Promise(resolve => {
  const popup = el('dialog', {}, el('h2',{},'Confirm action'), el('p',{},message));
  const done = answer => { popup.close(); popup.remove(); resolve(answer); };
  popup.append(el('div',{class:'actions'},button('Cancel',()=>done(false),'secondary'),button('Confirm',()=>done(true),'danger')));
  popup.addEventListener('cancel',event=>{event.preventDefault();done(false);}); document.body.append(popup); popup.showModal();
}); }
