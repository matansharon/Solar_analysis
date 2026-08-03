# Remove authentication from the web app

**Date:** 2026-08-03
**Status:** approved

## Goal

The web UI opens straight to the dashboard. No login page, no setup token, no
app password — on this dev machine *and* on the llmadmin deployment.

## Decision and its consequence

Auth is removed everywhere, not gated behind a dev-only flag, and the code is
deleted rather than left dormant. Re-enabling later means rewriting it.

`solar-analysis` on llmadmin (192.168.30.84:8010) is internal-network only with
no TLS. After this change, anyone who can reach that port can start runs, edit
plants, change settings, and read reports. Accepted deliberately: the network is
trusted and the app has a single operator.

## What is removed

**Backend**

- Delete `solaranalysis/web/auth.py` — password hashing, cookie signing, `RateLimiter`.
- Delete `solaranalysis/web/routes/auth.py` — `status`, `setup`, `login`, `logout`, `password`.
- `web/app.py` — remove `COOKIE`, `_PUBLIC`, `_authenticated()`,
  `_matches_known_route()`, the auth branch of the middleware, the rate limiter,
  the first-boot setup-token block, and the auth router include.
- `web/repo.py` — remove `get_session_epoch`, `bump_session_epoch`,
  `get_password_hash`, `set_password_hash`, `setup_required`,
  `get_setup_token_hash`, `set_setup_token_hash`, `clear_setup_token`.

**Frontend**

- Delete `src/auth.tsx` and `src/routes/LoginOrSetup.tsx`.
- `App.tsx` — drop `AuthProvider` and the `if (!authenticated)` gate; `Shell`
  renders nav + routes directly.
- `nav.tsx` — remove the "Log out" button; `styles.css` — its two rules.
- `Settings.tsx` — remove the change-password section.
- `api.ts` — remove `status`, `setup`, `login`, `logout`, `changePassword`,
  `AuthError`, and the 401 branch in `req()`.

## What is deliberately kept

**`web/crypto.py` and `app.state.key`.** The key signs session cookies *and*
encrypts the stored SolarEdge/Growatt/SMA credentials (`repo.py:117-177`).
`data/secret.key` must survive or those credentials become unreadable. Only the
cookie-signing use goes away.

**The `X-Solar-CSRF` header requirement** on POST/PUT/DELETE. Counter-intuitive
without sessions, but it is now load-bearing: with no login, any page a LAN
browser opens could POST `/api/runs` or DELETE `/api/plants/3`. Requiring a
custom header forces a CORS preflight that fails, so the browser never sends the
request. `api.ts` already sends it, so the cost is zero.

**Existing databases.** No migration. The orphaned `password_hash` and
`session_epoch` rows in `app.db` are inert; the stored password simply stops
being consulted.

## Tests

- Delete `tests/web/test_api_auth.py` and `tests/web/test_auth_primitives.py`.
- Seven files seed a setup token and POST `/api/auth/setup` in a local
  `_client()` helper — `test_api_plants`, `test_api_runs`, `test_api_schedules`,
  `test_api_plant_history`, `test_api_stream_report`, `test_importer`,
  `test_spa_serving`. Remove those two lines from each; keep the `CSRF` headers.

## Verification

`python -m pytest -q` green, `cd frontend && npm run build` clean (tsc catches
any missed `useAuth` import), and the dashboard reachable with no cookie.

## Docs

README's first-boot/setup-token section and the matching DEPLOYMENT.md step
describe a flow that no longer exists; both are updated.
