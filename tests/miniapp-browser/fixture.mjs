import {test as base, expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {once} from 'node:events';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');

export const test = base.extend({
  fixture: async ({page, context}, use, testInfo) => {
    const child = spawn(process.env.PYTHON || 'python3', ['tests/miniapp_browser_fixture.py'], {
      cwd: root,
      env: {...process.env, MINIAPP_FIXTURE_RECOVERY: '1'},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    child.stderr.on('data', chunk => {stderr += chunk;});
    const lines = createInterface({input: child.stdout});
    let startupTimer;
    const exited = once(child, 'exit');
    try {
      const ready = await Promise.race([
        once(lines, 'line').then(([line]) => JSON.parse(line)),
        exited.then(([code]) => {throw new Error(`Fixture exited ${code}: ${stderr}`);}),
        new Promise((_, reject) => {
          startupTimer = setTimeout(() => reject(new Error(`Fixture startup timed out: ${stderr}`)), 15000);
        }),
      ]);
      clearTimeout(startupTimer);
      const errors = [], posts = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => {
        if(request.method() === 'POST' && request.url().endsWith('/api/v1/memory/summary/generate')) {
          posts.push(request.postDataJSON());
        }
      });
      // No browser request reaches Telegram or any other external host.
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if(url.origin === 'https://telegram.org' && url.pathname === '/js/telegram-web-app.js') {
          return route.fulfill({contentType: 'text/javascript', body: ''});
        }
        if(url.origin !== ready.url) {
          errors.push(`Unexpected external request: ${route.request().url()}`);
          return route.abort();
        }
        return route.continue();
      });
      await page.addInitScript(initData => {
        window.Telegram = {WebApp: {
          initData, initDataUnsafe: {start_param: 'tools'},
          ready() {}, expand() {},
          BackButton: {onClick() {}, hide() {}, show() {}},
        }};
      }, ready.initData);
      const metrics = async () => {
        const response = await page.request.get(ready.url + '/fixture/metrics');
        expect(response.status()).toBe(200);
        return response.json();
      };
      const release = async () => {
        const response = await page.request.post(ready.url + '/fixture/release');
        expect(response.status()).toBe(200);
      };
      await page.goto(ready.url + '/miniapp/');
      await expect(page.locator('#connection-label')).toHaveText('Connected');
      await page.locator('.manage-link[data-page="memory"]').click();
      await expect(page.getByLabel('Summary', {exact: true})).toBeVisible();
      await use({url: ready.url, metrics, release, posts});
      expect(errors).toEqual([]);
    } finally {
      clearTimeout(startupTimer);
      // SIGTERM releases the test provider and drains this fixture's executor.
      if(child.exitCode === null) child.kill('SIGTERM');
      const timer = setTimeout(() => child.kill('SIGKILL'), 10000);
      await exited;
      clearTimeout(timer);
      lines.close();
      if(stderr) await testInfo.attach('fixture-stderr', {body: stderr, contentType: 'text/plain'});
    }
  },
});

export {expect};

export async function startSummary(page) {
  await page.getByRole('button', {name: 'Regenerate with Utility', exact: true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', {name: 'Confirm', exact: true}).click();
}

export async function expectOneExecution(fixture, jobId) {
  await expect.poll(async () => {
    const metrics = await fixture.metrics();
    return {handlers: metrics.handlers, providers: metrics.providers, jobs: metrics.jobs.length,
      id: metrics.jobs[0]?.id, operation: metrics.jobs[0]?.operation_id, state: metrics.jobs[0]?.state};
  }).toEqual({handlers: 1, providers: 1, jobs: 1, id: jobId,
    operation: fixture.posts[0].operation_id, state: 'succeeded'});
  expect(fixture.posts).toHaveLength(1);
}
