# Mini App browser validation

`tests/miniapp-browser/` runs the current Mini App in pinned Chromium and WebKit
engines through Playwright 1.63.0. The npm lock pins Playwright and its engine
revisions independently of runtime dependencies. These are development tests;
the installed bridge does not require Node, Playwright or browser binaries.

## Measured boundaries

Every test starts a fresh child of `tests/miniapp_browser_fixture.py` on an
ephemeral loopback port with a temporary database and synthetic Telegram
authentication. The page loads the actual HTML, CSS, ES modules, `app.js` API
adapter and operation controller. Browser requests reach the real aiohttp API,
SQLite job repository and utility executor. A counter wraps the actual
`regenerate_summary` handler without replacing its behavior. Only the external
provider response is synthetic. The recovery fixture seeds one short source
message, so a completed summary requires one handler and one provider call.

The Telegram host script is served an empty local test response after a
synthetic WebApp object is installed. Every other external browser request is
blocked and fails the test. No production configuration, credentials, databases,
private stories, Telegram messages or paid providers participate.

The six scenarios run in each engine:

1. Accept a real summary POST with `route.fetch()`, then abort delivery to the
   page. Navigate to Characters and recover through the surviving control.
   Assert the by-operation read returns the original saved job, one browser
   POST, one durable record, one real handler and one provider call.
2. Abort the first known-job GET, then let the original worker finish. Assert
   bounded reconnect returns the saved summary and every poll uses the same ID.
3. Abort four consecutive known-job reads, exhausting the three backoffs. Assert
   the manual tracking control appears, then recovers the original job without
   another POST or execution.
4. Use a 390 × 844 viewport, actual navigation and native HTML dialog. Assert
   heading focus, no page overflow, initial Cancel focus, Escape cancellation,
   restored keyboard focus and zero submitted work after cancellation.
5. Complete a confirmation while the user has deliberately focused another
   editor. Assert the pending action does not take focus or change the draft.
6. Complete a confirmation after navigation removes its opener. Assert the new
   page heading keeps focus.

The last two scenarios exercise the exported real UI primitives with a
test-owned pending action. They do not replace the dialog implementation or
introduce production-only test hooks. The focus fix associates a confirmation
opened synchronously before the action's first await with its exact initiating
button. It restores that button after it is enabled only when it remains
connected and the document body still owns focus. Confirmations opened later
after an unrelated asynchronous read retain their existing native behavior.

A separate Python regression exercises the fixture over real HTTP without a
browser. It discards the accepted response body, recovers the saved summary,
checks repeated POST deduplication, and verifies the actual handler/provider
counters. This checks that browser instrumentation does not silently bypass
the production execution path.

## Run and inspect

Install the checked-in Python runtime and development locks with Python 3.11,
then run:

```bash
npm ci --prefix tests/miniapp-browser --include=dev --ignore-scripts --no-audit --no-fund
node tests/miniapp-browser/node_modules/@playwright/test/cli.js install --with-deps chromium webkit
PYTHON=/path/to/python3.11 npm test --prefix tests/miniapp-browser
python -X dev -W error::ResourceWarning -m pytest -q tests/test_miniapp_browser_recovery_fixture.py
```

One worker runs both projects sequentially. Assertions, network events and the
provider release gate coordinate progress; the tests add no arbitrary sleeps
and keep the production polling/backoff intervals. The child releases blocked
provider calls, drains its executor and closes its server on SIGTERM. Each
Playwright context and process is isolated from the next test.

Normal `miniapp-smoke` CI still runs the existing DOM suite and loopback smoke,
then installs both engine revisions and runs all twelve browser cases. Neither
engine is skipped. Its protected aggregate dependency is unchanged. Logs,
the HTML report, and failure screenshots/traces are uploaded for 14 days.
The job timeout is ten minutes to include browser/system-library installation.

## Recorded local evidence and limits

On 2026-10-07, Python 3.11.16 and Node 24.19.0 in the isolated workspace produced:

- **PASS:** six Chromium cases, Chromium 153.0.8010.12, Playwright revision 1243.
- **PASS:** the real-handler/provider Python regression with ResourceWarnings
  treated as errors; the focused fixture/recovery and CI contract run passed
  38 tests.
- **PASS:** all 66 existing DOM unit tests after integration with main
  `f2482bd5de85bf9de9e3e0574c75fd3d75dd16c5` (63 passed before that integration).
- **PASS:** existing loopback DOM smoke: 19 pages, five mutations, stale-session
  rejection, saved optimizer preview recovery, and zero browser errors.
- **PASS:** whole-tree Ruff and formatting checks; the Python module-size gate.
- **RED reproduced and fixed:** native Escape cancellation lost focus because
  the initiating action button remained disabled while the dialog closed.
- **NOT RUN to completion locally:** WebKit 26.6, revision 2359. Its archive
  downloaded, but launch reported missing GTK, GStreamer and other libraries.
  The normal system-dependency installer failed on this workspace's restricted
  `setgroups`/UID system calls. No sandbox or apt security settings were changed.

The primary Chromium CDN returned a short HTML response instead of an archive.
The exact Chrome for Testing headless-shell archive was obtained from its
official Google distribution for the pinned version and used without changing
the checked-in browser configuration. This was an environment download issue,
not a passing claim for the failed standard installer.

These measurements are local evidence. Exact-head normal CI must pass both
engines before merge; a collected test list or a downloaded WebKit archive
does not prove WebKit execution. The initial remote VPS session became
unresponsive before this implementation was transferred there, so VPS results
for these new tests are not claimed.

**Native Telegram Android/iOS acceptance remains pending.** Linux Chromium or
WebKit with a mobile viewport does not establish Telegram WebView behavior,
device viewport/keyboard integration, native Telegram APIs or model-answer
quality. Keep issue #410 open until that acceptance and the other recorded
checkpoints are completed with their own evidence.
