import {test,expect} from './fixture.mjs';

test('navigation aborts delayed portraits and the next character page still loads',async({page,fixture})=>{
  const pattern='**/api/v1/characters/*/portrait',held=[],aborted=[];
  let release;
  const released=new Promise(resolve=>{release=resolve;});
  page.on('requestfailed',request=>{
    if(request.url().endsWith('/portrait'))aborted.push(request.url());
  });
  await page.evaluate(()=>{
    const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
    window.portraitResources={created:[],revoked:[]};
    URL.createObjectURL=blob=>{const url=create(blob);window.portraitResources.created.push(url);return url;};
    URL.revokeObjectURL=url=>{window.portraitResources.revoked.push(url);return revoke(url);};
  });
  await page.route(pattern,async route=>{
    const response=await route.fetch();
    held.push(route.request().url());
    await released;
    if(!route.request().failure())await route.fulfill({response});
  });
  try {
    await page.getByRole('button',{name:'Characters',exact:true}).click();
    await expect(page.locator('.modern-portrait')).toHaveCount(2);
    await expect.poll(()=>held.length).toBe(2);
    await page.getByRole('button',{name:'Settings',exact:true}).click();
    await expect(page.locator('#page-title')).toHaveText('Settings');
    await expect.poll(()=>aborted.length).toBe(2);
    release();
    await page.unroute(pattern);
    expect(await page.evaluate(()=>window.portraitResources.created)).toEqual([]);
    await page.getByRole('button',{name:'Characters',exact:true}).click();
    await expect.poll(()=>page.locator('.modern-portrait').evaluateAll(images=>
      images.length===2&&images.every(image=>image.complete&&image.naturalWidth>0))).toBe(true);
    const resources=await page.evaluate(()=>window.portraitResources);
    expect(resources.created).toHaveLength(2);
    expect(resources.revoked.toSorted()).toEqual(resources.created.toSorted());
    expect((await fixture.metrics()).providers).toBe(0);
  } finally {release();await page.unroute(pattern);}
});
