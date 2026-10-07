import assert from 'node:assert/strict';
import test from 'node:test';
import {createPage,until} from './dom.mjs';

test('Pending buttons reject duplicate dispatched actions',async()=>{
  const page=await createPage('ui.js');
  try {
    let calls=0,finish;const pending=new Promise(resolve=>{finish=resolve;});
    const control=page.namespace.button('Save',async()=>{calls++;await pending;});page.document.body.append(control);
    control.dispatchEvent(new page.window.Event('click'));control.dispatchEvent(new page.window.Event('click'));
    assert.equal(calls,1);assert.equal(control.getAttribute('aria-busy'),'true');
    finish();await until(()=>!control.disabled);assert.equal(control.hasAttribute('aria-busy'),false);
  } finally {page.close();}
});

test('Operation errors persist until dismissed and stale forms have an explicit recovery',async()=>{
  const page=await createPage('ui.js');
  try {
    const scheduled=[];page.window.setTimeout=callback=>{scheduled.push(callback);return scheduled.length;};
    const action=page.namespace.button('Save',()=>{throw Object.assign(new Error('Session changed.'),{status:409});});page.document.body.append(action);action.click();
    await until(()=>!action.disabled);
    const notice=page.document.getElementById('notice');assert.equal(notice.getAttribute('role'),'alert');
    assert.match(notice.textContent,/refresh/i);
    for(const callback of scheduled)callback();assert.equal(notice.hidden,false,'An error cannot disappear on the success-notice timer');
    const dismiss=notice.querySelector('button');assert.ok(dismiss,'Persistent errors can be dismissed');dismiss.click();assert.equal(notice.hidden,true);
  } finally {page.close();}
});
