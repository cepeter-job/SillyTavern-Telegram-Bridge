# Recovering an interrupted Mini App operation

A disconnected browser does not cancel a job already accepted by the bridge.
Do not submit the same action again merely because its result is missing.

When the initial response is lost, choose **Recover original operation**. The
Mini App looks up the existing actor-owned operation ID; it does not send a
replacement POST. When a job ID is already known, choose **Continue tracking**
to read that same job. Temporary polling failures first receive three bounded
retries (0.5, 1.5 and 3 seconds). Tracking pauses after ten minutes; manual
tracking starts another bounded observation of the same operation.

Recovery is available outside the page's editor, so an error message or a page
navigation cannot erase the recovery control. Recovering a result does not
silently refresh an editor or discard a draft. Review the result, then use the
explicit refresh action when it is safe to discard unsaved edits.

## Boundaries

Recovery handles are kept in the current document, for at most one hour, with a
hard limit of 32 handles. Completed handles can be evicted to make room; pending
handles are never silently replaced. The original request is snapshotted before
submission and identified by its SHA256 digest. Request bodies and Telegram
credentials are never placed in localStorage or sessionStorage.

After closing or reloading the app, use **Settings → System → Operations** to
inspect durable jobs and saved previews. This is not automatic cross-reload
request replay. Authentication failures require reopening from Telegram. A
missing or pruned job, an expired handle, a changed actor, or an unrecognized
server status never authorizes another POST. Review the saved story before
starting a genuinely new action.

Server-side history is bounded independently: terminal records are pruned on
new admission after their creation age exceeds 24 hours or they fall outside
the newest 100 records for the actor. The recent list shows 20 entries. The
client's one-hour window is an observation limit, not a guarantee that a terminal
server record survives pruning. A confirmed failure or interrupted job is shown
as a terminal outcome, not retried automatically.

## API contract

`GET /api/v1/jobs/by-operation/{operation_id}` is authenticated, same-origin and
actor-bound. It only looks up existing SQLite records and returns the same job
shape as `GET /api/v1/jobs/{job_id}`. Unknown and other-actor IDs return 404. No
schema creation, work admission, provider request or source mutation occurs in
this lookup. Submitting the same actor, operation ID and exact payload still
uses the existing server deduplication contract; a changed payload conflicts.
