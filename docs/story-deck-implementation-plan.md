# Story Deck implementation plan

Approved direction: Story Deck (01) with Director Studio (03) role, decision and
failure clarity. Baseline: `f0dcf7a55add60376bc1d9eae9fb5ded5168656d`.

## Scope and contracts

- Keep the vanilla JavaScript/CSS stack, authenticated local API, Telegram theme
  and safe-area integration. Use actual session data and character portraits.
- Home leads with the active story, chat, saved trackers and private Director.
  Four primary destinations are Home, Characters, Tools and Settings. Existing
  page/start-parameter keys remain supported and every current capability stays
  reachable. Director, Trackers and Sessions return to Home; Models and System
  return to Settings; knowledge/setup tools return to Tools.
- Check and Imagine are explicit Telegram command handoffs, since no MiniApp
  execution API exists. Closing the MiniApp does not send those commands.
- Story, Utility and Director are configured roles. Show effective inheritance,
  never invented provider health or a configurable fallback role.
- Tracker reads remain read-only. Director history has no timestamps. Keep
  private plans separate from saved facts, session/revision guards, confirmations,
  ending controls and independent reasoning settings.
- Errors retain useful input and session context. Uncertain jobs route to
  Operations, without inviting duplicate submission.

## Execution and ownership

1. Record this plan and establish focused failing behavior tests.
2. Visual owner: global CSS and Home in `system.js`; retain diagnostics/update
   behavior. Match selected dark tokens with a readable light adaptation.
3. Director owner: `director.js`, `trackers.js` and their focused DOM tests.
4. Models owner: `models.js`, `characters.js` and their focused DOM tests.
5. Integrator: shell, navigation, shared helpers, hub pages, test harness, docs.
6. Run focused tests, full existing DOM/API regression suite, narrow/wide browser
   checks in both themes, repository gates and independent review.
7. Open PR, require passing checks for the exact reviewed head, merge and verify
   main. Remove this completed plan from current docs, retaining it in history.

No backend feature expansion, new frontend runtime dependency, production data,
release or deployment is part of this change.
