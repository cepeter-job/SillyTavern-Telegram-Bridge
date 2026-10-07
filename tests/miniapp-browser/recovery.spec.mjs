import {test, expect, startSummary, expectOneExecution} from './fixture.mjs';

// Break caught: replacing an unresolved action with a fresh POST, losing the
// editor-independent recovery control, or returning a different saved job.
test('accepted POST response loss recovers the original job without another execution', async ({page, fixture}) => {
  let accepted, lookedUp;
  await page.route('**/api/v1/memory/summary/generate', async route => {
    const response = await route.fetch({maxRetries: 0});
    expect(response.status()).toBe(200);
    accepted = await response.json();
    await route.abort('failed');
  });
  await page.route('**/api/v1/jobs/by-operation/*', async route => {
    const response = await route.fetch({maxRetries: 0});
    lookedUp = await response.json();
    await route.fulfill({response});
  });
  await startSummary(page);
  const recovery = page.getByRole('button', {name: 'Recover original operation', exact: true});
  await expect(recovery).toBeVisible();
  await expect.poll(async () => (await fixture.metrics()).providers).toBe(1);
  // A real page navigation replaces #content while the recovery control survives.
  await page.getByRole('button', {name: 'Characters', exact: true}).click();
  await expect(page.locator('.character-page-header')).toBeVisible();
  await expect(recovery).toBeVisible();
  await fixture.release();
  await recovery.click();
  await expect(page.locator('.recovery-completed')).toBeVisible();
  await expect(page.locator('.recovery-completed')).toContainText('Synthetic lighthouse continuity');
  expect(lookedUp.id).toBe(accepted.id);
  await expectOneExecution(fixture, accepted.id);
  await expect(page.locator('.character-page-header')).toBeVisible();
});

// Break caught: ending tracking on a single transient GET failure or polling
// a replacement job instead of the originally accepted ID.
test('transient polling failure reconnects to the same job and saved summary', async ({page, fixture}) => {
  let failures = 0;
  const reads = [];
  await page.route(/\/api\/v1\/jobs\/[a-f0-9]{32}$/, async route => {
    reads.push(route.request().url().split('/').at(-1));
    if(failures === 0) {
      failures++;
      await fixture.release();
      return route.abort('failed');
    }
    return route.continue();
  });
  await startSummary(page);
  await expect(page.getByLabel('Summary', {exact: true})).toHaveValue(/Synthetic lighthouse continuity/);
  expect(failures).toBe(1);
  expect(reads.length).toBeGreaterThanOrEqual(2);
  expect(new Set(reads).size).toBe(1);
  await expectOneExecution(fixture, reads[0]);
  await expect(page.locator('.operation-recovery')).toHaveCount(0);
});

// Break caught: unbounded retries, losing the known job after the retry budget,
// or manual resume replaying the write.
test('bounded polling failures offer manual tracking of the original job', async ({page, fixture}) => {
  let failures = 0;
  const reads = [];
  await page.route(/\/api\/v1\/jobs\/[a-f0-9]{32}$/, route => {
    reads.push(route.request().url().split('/').at(-1));
    if(failures < 4) {failures++; return route.abort('failed');}
    return route.continue();
  });
  await startSummary(page);
  const resume = page.getByRole('button', {name: 'Continue tracking', exact: true});
  await expect(resume).toBeVisible();
  expect(failures).toBe(4);
  expect(reads).toHaveLength(4);
  await expect(page.locator('.operation-recovery')).toContainText('Current status is unknown');
  await fixture.release();
  await resume.click();
  await expect(page.locator('.recovery-completed')).toContainText('Synthetic lighthouse continuity');
  expect(new Set(reads).size).toBe(1);
  await expectOneExecution(fixture, reads[0]);
});

// Break caught: clipping the primary shell on narrow viewports, dropping heading
// focus during navigation, or a cancelled real modal still submitting work.
test('mobile navigation and native dialog retain keyboard focus without submitting on cancel', async ({page, fixture}) => {
  await expect(page.locator('#page-title')).toHaveText('Memory');
  await expect(page.locator('#page-title')).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const regenerate = page.getByRole('button', {name: 'Regenerate with Utility', exact: true});
  await regenerate.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', {name: 'Cancel', exact: true})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(regenerate).toBeFocused();
  await expect(regenerate).toBeEnabled();
  expect(fixture.posts).toHaveLength(0);
  expect(await fixture.metrics()).toMatchObject({handlers: 0, providers: 0, jobs: []});
  await page.getByRole('button', {name: 'Settings', exact: true}).click();
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await expect(page.locator('#page-title')).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

async function pendingConfirmation(page) {
  // Exercise the exported UI primitive with a pending action; do not replace
  // the native dialog or its focus behavior with a browser mock.
  await page.evaluate(async () => {
    const {button, confirmAction} = await import('/miniapp/ui.js');
    const control = button('Fixture confirmation', async () => {
      if(await confirmAction('Synthetic focus check')) {
        await new Promise(resolve => {window.finishFocusCheck = resolve;});
      }
    });
    document.getElementById('content').append(control);
  });
  const control = page.getByRole('button', {name: 'Fixture confirmation', exact: true});
  await control.focus();
  await page.keyboard.press('Enter');
  await page.getByRole('dialog').getByRole('button', {name: 'Confirm', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(control).toBeDisabled();
  return control;
}

// Break caught: restoring an old modal opener after the user deliberately
// focuses another editor while the confirmed asynchronous action finishes.
test('confirmed action preserves focus deliberately moved to another editor', async ({page, fixture}) => {
  const control = await pendingConfirmation(page);
  const summary = page.getByLabel('Summary', {exact: true});
  await summary.fill('Synthetic unsaved draft');
  await summary.focus();
  await page.evaluate(() => window.finishFocusCheck());
  await expect(control).toBeEnabled();
  await expect(summary).toBeFocused();
  await expect(summary).toHaveValue('Synthetic unsaved draft');
  expect(fixture.posts).toHaveLength(0);
});

// Break caught: restoring a disconnected control after navigation, instead of
// retaining the new page heading's keyboard focus.
test('confirmed action cannot restore an opener removed by navigation', async ({page, fixture}) => {
  await pendingConfirmation(page);
  await page.getByRole('button', {name: 'Settings', exact: true}).click();
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await page.evaluate(() => window.finishFocusCheck());
  await expect(page.getByRole('button', {name: 'Fixture confirmation', exact: true})).toHaveCount(0);
  await expect(page.locator('#page-title')).toBeFocused();
  expect(fixture.posts).toHaveLength(0);
});
