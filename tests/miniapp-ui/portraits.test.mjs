import test from 'node:test';
import assert from 'node:assert/strict';
import {createPage,until} from './dom.mjs';

const session={session_id:'story-a',title:'Story A',character_file:'Alice.png',mode:'normal'};
const catalog={session,total:1,characters:[{name:'Alice',filename:'Alice.png',digest:'a'.repeat(64)}]};

async function portraits() {
  const pending=[],created=[],revoked=[];
  const page=await createPage('characters.js',{api:async(path,options)=>{
    if(path.startsWith('/characters?'))return catalog;
    if(path.endsWith('/portrait'))return new Promise(resolve=>pending.push({resolve,signal:options.signal}));
    throw new Error('Unexpected API '+path);
  }});
  page.window.URL.createObjectURL=()=>{const url='blob:portrait-'+created.length;created.push(url);return url;};
  page.window.URL.revokeObjectURL=url=>revoked.push(url);
  const owner=new page.window.AbortController();
  const root=await page.pages.characters({signal:owner.signal});
  page.document.getElementById('content').append(root);
  const settle=async(index=0)=>{
    pending[index].resolve(new page.window.Blob(['portrait']));
    await new Promise(resolve=>setTimeout(resolve,0));
  };
  return {...page,pending,created,revoked,owner,root,settle};
}

test('leaving a page cancels portrait requests and ignores a late successful response',async()=>{
  const page=await portraits();
  try {
    page.root.remove();page.owner.abort();
    await page.settle();
    assert.deepEqual(page.created,[],'A detached page cannot allocate a new portrait Blob URL');
    assert.equal(page.pending[0].signal.aborted,true,'Navigation also releases pending network work');
  } finally {page.close();}
});

test('leaving a page releases a portrait that has not emitted load or error',async()=>{
  const page=await portraits();
  try {
    await page.settle();
    assert.equal(page.created.length,1);
    assert.deepEqual(page.revoked,[]);
    page.root.remove();page.owner.abort();
    assert.deepEqual(page.revoked,page.created,'Lazy or unmounted images must not retain their Blob URLs');
  } finally {page.close();}
});

test('replacing the character list disposes its old portraits without cancelling the new view',async()=>{
  const page=await portraits();
  try {
    page.document.querySelector('.character-search button').click();
    await until(()=>page.pending.length===2,'replacement character list');
    await page.settle();
    assert.deepEqual(page.created,[],'The retired list cannot allocate late portrait resources');
    assert.equal(page.pending[0].signal.aborted,true);
    assert.equal(page.pending[1].signal.aborted,false);
    await page.settle(1);
    assert.equal(page.created.length,1,'The new list still loads its portrait');
  } finally {page.owner.abort();page.close();}
});

test('a normal portrait load releases its Blob URL once and keeps the displayed image',async()=>{
  const page=await portraits();
  try {
    await page.settle();
    const image=page.document.querySelector('.modern-portrait');
    image.dispatchEvent(new page.window.Event('load'));
    assert.deepEqual(page.revoked,page.created);
    assert.equal(image.getAttribute('src'),page.created[0]);
    page.owner.abort();
    assert.equal(page.revoked.length,1);
  } finally {page.close();}
});
