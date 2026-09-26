# Resonance v13 Final Release Status

Updated 2026-09-26; first written 2026-09-25 after the first end-to-end execution of the
cross-container suite in this delivery environment. Earlier revisions of this file stated that the environment
"does not expose a Docker daemon" and that the Compose E2E had to be run elsewhere; that
was true of the packaging environment but not of this one, and it had stood in place of
evidence for every gate in `RELEASE_CHECKLIST.md`. The gates are now recorded against what
was actually executed.

## Executed on a real Compose stack

Authoritative run: `scripts/acceptance-all.sh` on a fresh database (volumes removed
before start), **19 steps PASS and 1 recorded as skipped** across 20 rows, commit
`1f19952`, 2026-09-26T09:58:44Z → 2026-09-26T10:16:39Z, evidence in
`release-evidence/acceptance-20260926T095844Z/` (per-step logs, `SUMMARY.txt`, full
`compose-logs.txt` and `commercial-compose-logs.txt`), with the browser audit's own
machine-readable record at `release-evidence/browser-a11y-20260926T101104Z/report.json` (stamped with the same commit —
this round the code was committed *before* the authoritative run was started, so the `git_commit` in the record is the
tree that was actually tested rather than HEAD-plus-staged-changes).
What is version-controlled from that directory is only `SUMMARY.txt` (per-step verdicts and timestamps) and the
browser run's `report.json`; the per-step logs and the two compose log files stay on the host that ran the pipeline.
So a clone can re-check the step ledger and the whole browser reading (views, scans, violations per impact, CSP,
`uncaught_errors`, the second-factor states) against committed artifacts, while the drill tallies and timings quoted
below come from per-step logs that exist only on that host -- re-running the pipeline is how a reader verifies those.
The pipeline is 20 rows wide now: `mfa-drill` joined at step 12 and `member-drill` at step 13, which
is also where the erasure drill's step 11 reading comes from.
Eleven green runs precede it on this host
(`82f2ffe`, `5028686`, `28deafc`, `1d8534e`, `01e61d1`, `2c3ef7f`, `96d5955`, `b79e70b`, `3da3920`, `93b4984`, `99d5847` —
older → newer, at 15/15/16/16/17/18/19/20/20/20/20 rows with `FAIL=0` in every `SUMMARY.txt`), so the pass is reproducible rather
than a single lucky run. The pipeline widened as steps were added, so what those runs share is "each
passed every row that existed then", not "the same 20 rows seven times".
Two discovery records from today are kept because each is the evidence for a defect that is now fixed:
`acceptance-20260926T021711Z` stops at step 3 because the acceptance suite signed a provider webhook with
its own copy of the secret literal while the container had never been given the variable, so a changed
deployed value turned it into a 401; `acceptance-20260926T022643Z` stops at step 17 with 98/100 and two
candidates whose recorded reason was the unreadable `{'type': 'ReadError', 'message': ''}`.

> **CI status is deliberately not claimed as evidence.** The branch was pushed, which
> dispatches those jobs, but this environment cannot read their outcome: the repository is
> private (unauthenticated fetch returns 404), the `gh` CLI is not logged in, the GitHub MCP
> connector exposes no workflow-run tool, and the local browser has no GitHub session. Treat
> the CI jobs as dispatched-and-unverified; every number quoted in this file comes from the
> local run above.

- Compose E2E, twice — including once **after** a backup/restore: 11 resident cases over
  live api/worker/web/admin/gateway.
- Provider and payment contract tests: 5 cases (the provider file gained a case that posts
  the vendor-facing dialect, and one that requires a contract-violating body to be refused).
- Worker kill -9 lease recovery: existing drill, passing.
- **Two-worker lease contention** (new): `worker-b` claimant + 8 jobs →
  `8 jobs, 2 workers, 160.0 credits settled once`. Claimants are read from
  `generation_attempts`, because `generation_jobs.lease_owner` is cleared once a job
  settles and would have reported zero contenders as a pass.
- **Absolute restore fidelity** (new): ledger balances per account and the sha256 of every
  asset's exported master audio, taken before backup and re-checked after restore — this
  run measured `2 workspaces, 2 assets byte-identical, ledgers unchanged` (an earlier run
  reported 4 assets; the count is just how many assets that database held, the criterion
  is unchanged and refuses to pass on a zero denominator). The check was shown to bite:
  deleting a fingerprinted master object turns it red (`export 500`), restoring turns it
  green again.
- Commercial flow: licence → offer → purchase → payment → delivery package → seller payout
  → refund → reversal, plus the brand-award variant.
- **Cross-tenant isolation** (new): 10 cases against three owner-role tenants, covering
  ~20 read routes, media-token minting, foreign writes, ledger scoping and the
  seller-OR-buyer counterparty policy.
- **Exclusive-reservation race** (new): see the defect below.
- **Market reconciliation** (new): 15 invariants across order / licence / delivery /
  revenue split / payout and the exclusive-offer listing states, read twice (database as
  authority, HTTP API as what the product shows), plus 8 pure-function unit tests proving
  the 85/15 policy checks can fail.
- **100-run generation regression** (new): `100/100 completed, error rate 0.0%`, per-job
  ready count, 64-hex media hash, sampled real download, `settled_credits == quoted price`,
  and a ledger that moved by exactly the summed settlement with no dangling hold.
  Across all eleven green runs, in run order: **p50 8.18s / p95 20.77s**, **p50 7.615s / p95 11.37s**,
  **p50 6.25s / p95 8.31s**, **p50 8.525s / p95 13.64s**, **p50 5.195s / p95 9.89s**,
  **p50 7.18s / p95 15.66s**, **p50 5.09s / p95 5.37s**, **p50 4.09s / p95 5.33s**, **p50 5.06s / p95 5.26s**,
  **p50 6.84s / p95 8.21s** and **p50 5.215s / p95 7.16s** (the authoritative run, step 17) -- every one of them `100/100 completed, error rate 0.0%` with 1000
  credits settled, so the count is constant while the timing spans 4-21s;
  the red run at `1f8010e` read p50 31.88s / p95 81.81s on the same code path with 87/100
  finished. The timing reading moves with host I/O, so it must be quoted per run, not as a
  property of the code.
- **Third-party provider adapter round trip** (new): `docker-compose.generic-rest.yml` sets
  `MUSIC_PROVIDER=generic_rest` so `GenericRESTAdapter` -- not the emulator's own adapter --
  drives generation, and `scripts/provider_regression.py` now refuses the batch unless
  `/api/bootstrap` reports that identity (measured both ways: with the overlay off it exits
  `expected provider 'generic_rest', the stack reports 'emulator' ... the overlay did not take
  effect`; with a dead base URL it fails a job and prints the reason the worker stored,
  `job_error={'type': 'ConnectError', ...}`). The step has run six times and read `25/25 completed,
  error rate 0.0%, settled 250 credits` every time (p50/p95 5.21s/6.37s at step 16 of `2c3ef7f`,
  4.14s/4.19s at step 17 of `96d5955`, 4.14s/4.18s at step 18 of `b79e70b`, 4.11s/4.2s at step 18 of
  `3da3920`, 5.14s/6.34s at step 18 of `93b4984`, 4.11s/5.23s at step 18 of `99d5847` and 5.17s/6.31s at step 18 of the authoritative run), so the
  count is the reading that travels and the timing is not. This is the adapter and contract plumbing, not
  a provider: the endpoint behind it is still our emulator, so the gate for 100 *real*
  provider runs stays open.
- **Real-browser walkthrough + axe audit** (new): Playwright driving axe-core 4.13.0
  (pinned by sha256, fetched at run time, test-only) against the live stack —
  **96 view records, 70 axe scans across desktop 1440 and phone 390, 0 critical and 0
  serious**, 52 moderate left as follow-up (`heading-order` 36, `landmark-one-main` 8, `region` 8 — an earlier
  revision of this line said 96 moderate with `region` 56, and 48 of those were the shadow of one defect: the login
  screen kept rendering underneath every signed-in view because `label { display: grid }` outranks the UA's
  `[hidden]` rule; see the round section at the end of this file).
  It also asserts what axe cannot see: the shipped CSP must block **zero** inline styles
  authored by the app (measured 0 after the fix, 10 per radar render before), no uncaught
  exceptions, `script-src 'self'` present on both origins, skip-link and keyboard access
  to the nav, and no horizontal overflow at 390px.
- **Subject-rights walk inside that audit** (new): `walk_privacy` logs in as a probe account the gate
  creates for the step and exercises both rights in the browser -- a real file download (2614 and 2613
  bytes, parsed and checked that its `account.id` is the requesting session's own, that `coverage` is
  non-empty and that `password_hash` is declared excluded and absent from the payload), the confirmation
  gate in both directions, the self-erasure itself, the receipt the app paints on the login screen after
  the session dies, and the same credentials refused on the same form. Readings: 2 exports parsed, 2 probe
  accounts erased, `privacy_states` = panel / exported / erased-receipt, once per viewport. Provisioning
  the probe is not best-effort: a run that cannot create one is reported red, because skipping silently
  would delete the destructive half of the coverage.
- **Subject access export + right to erasure drill** (new): `scripts/erasure_drill.py` provisions
  a SQL fixture account (there is no registration endpoint), drives
  `GET /api/account/export` and `POST /api/account/erasure` over the live API, and checks both
  polarities of every guard: a wrong confirmation is refused and changes nothing; a sole
  workspace owner is refused and keeps its session; a platform administrator is refused; the
  matching confirmation revokes the live session without touching its immutable `expires_at`,
  removes memberships and preferences, anonymises `email`/`display_name`, and survives a
  re-run as a no-op. Access is then re-probed (token 401, login 401), retention is re-probed
  by *attempting* to delete the surviving provenance and requiring the database to refuse
  (`ERROR: song_spec_revisions is immutable`), and coverage is re-probed against
  `information_schema`: the export declares the `table.column` stores it reads, and the drill
  subtracts every foreign key in the live schema that points at `users(id)`. That check was
  shown to bite — a throwaway `tmp_probe_link(user_id REFERENCES users(id))` table made it name
  `tmp_probe_link.user_id` and fail, and dropping the table made it pass again.
- **Second-factor drill** (new, step 12): `scripts/mfa_drill.py` — `54/54 checks` in the authoritative
  run, and the codes come from `scripts/e2e_client.py`'s own stdlib RFC 6238 implementation rather than
  from the pyotp the server uses, so a green run cannot be two halves of the same mistake. The four
  assertions that carry weight are listed in the round section below; the drill also waits out the
  fixed challenge window by reading the Redis key TTL instead of sleeping a guess. Two of the three new
  assertions are an ordering property: dropping the factor now needs the password as well, the password is
  checked *first*, and a refusal at that wall must leave the one-time code unspent — the drill sends the same
  code twice, sees 403 the first time and 200 the second, which is what makes "the code was not consumed" a
  measurement rather than an assumption.
- **Workspace membership drill** (new, step 13): `scripts/member_drill.py` — `47/47 checks`, including
  two that issue a raw `INSERT` and a self-promoting `UPDATE` as the application role and require the
  database to answer `permission denied`, and the loop that closes the erasure dead end: sole owner →
  erasure refused → transfer → erasure succeeds. Eight of those are the step-up wall: a role change without
  the password is 401 and changes nothing, with a wrong password it is 403 and changes nothing, the
  credential is spent *before* the workspace rule is consulted, transferring ownership asks for it too, and
  at the end six wrong passwords on one account make the seventh attempt — with the *correct* password —
  answer 429, so the wall is on a clock and not on luck.
- Static verification and unit tests: 210 unit tests (196 at the previous authoritative run `99d5847`, 189 at the one before that `93b4984`, 183 before that; this file had recorded 23, then 54, 66, 70, 78 and 196 in earlier revisions), of which 36 arrived with the provider contract: 26 on the generic
  REST adapter's own surface and 10 validating the adapter's payload against the written
  schema.

## Defects found and fixed by that execution

1. **An exclusive asset could be sold twice.** A buyer whose reservation had already been
   expired by a later purchaser could still pay the stale order and be issued a second
   active licence; the race reproduced with 2 active licences on one exclusive asset. Fixed
   by `db/migrations/011` (`confirm_marketplace_offer_reservation`, `SECURITY DEFINER`,
   source-state predicates) following the same pattern as `reserve_marketplace_offer`.
   Two root causes: fulfilment had no source-state predicate at all, and — because
   `offer_reservations`/`asset_offers` are tenant-protected while the payer is not the
   seller's tenant — the previous direct `UPDATE`s matched **zero rows and raised nothing**,
   so an exclusive offer was never actually flipped to `sold`.
2. **`GET /orders` reported the wrong money amount.** The window count was aliased
   `total`, and `o.*` already carries `orders.total`, so every listed order's amount was
   replaced by the page row count — which the order list in `services/web/app.js:1498`
   rendered as the price paid. The alias is now `row_total`. `information_schema` confirms
   `orders`/`order_items` are the only tables with a `total` column, so no other list
   endpoint was affected. The pre-existing unit test had pinned the clobbered shape and was
   corrected to the real one.
3. **`apt-get` in the worker image could not reach `deb.debian.org` over plain HTTP:80**
   on this network, failing the build at ffmpeg; indexes are now fetched over TLS. Gateway
   and Prometheus host ports became overridable because another stack on this host owns
   8080/9090.
4. `static-verify.sh` crashed (rather than failing) when backup output was present, because
   its `**/*.json` walk met a MinIO directory named `.usage.json`; it now prunes artifact and
   hidden directories, with the pruned walk verified to still cover all 20 tracked JSON files.
5. **A refunded exclusive licence left its listing stuck at `sold`.** Same root cause as #1's
   second half: `market.py`'s refund branch flipped the seller's offer from the buyer's
   transaction, so under `FORCE ROW LEVEL SECURITY` the UPDATE matched zero rows and raised
   nothing. Reproduced as 14/15 in the reconciliation drill; `db/migrations/012`
   (`pause_marketplace_offer_on_refund`, `SECURITY DEFINER`) lifts it to `paused`, 15/15.
6. **The app's own CSP disabled parts of the app.** `style-src 'self'` rejects inline styles
   and `style=` attributes, and `app.js` used both in ten places: the quality radar's group
   dots measured `rgba(0,0,0,0)`, the data polygon lost its fill, the hover tip never moved,
   and the audio progress bar never advanced. Rendering the radar emitted 10 CSP violations
   **with no test tooling in the page** — attribution was made by re-running the identical
   flow without axe, which separated 10 app-authored violations from 34 the scanner caused
   by injecting its own styles (blocked, and previously reported as app console errors).
   Fixed without weakening the policy: tone classes for colour, SVG `transform` attributes
   for the tip, a native `<progress value>` for playback. `browser_a11y.py` now fails if the
   app authors a single blocked inline style, so this cannot silently return.
7. **The UI proxy kept dialling a dead API address.** `proxy_pass http://api:8000` with a
   literal hostname is resolved once at config load; when the commercial overlay recreated
   `api`, nginx continued to target the previous IP (`upstream: "http://172.19.0.8:8000"`,
   api actually `172.19.0.7`) and every `/api/` call from Studio or the Control Plane
   returned 502 while the API itself was healthy. Both proxy blocks now use Docker's
   embedded resolver with a variable; re-verified by forcing an address change
   (`.7 → .13`) with the web container never restarted — three consecutive logins through
   the proxy returned 200.
8. **The error path destroyed the reason.** Both frontends did `await r.json()` and, in the
   `catch`, `await r.text()` on the already-consumed stream, so the user-visible message was
   `Failed to execute 'text' on 'Response': body stream already read` instead of whatever
   the server had said. Fixed to read the body once; that fix is what surfaced
   `"too many login attempts"` and made #7's rate-limiter look-alike diagnosable.
9. **Accessibility defects invisible to attribute counting.** The Control Plane's workspace
   `<select>` had no accessible name (axe: `select-name`, critical), and the topbar's grid
   items could not shrink below min-content, forcing 435px of layout into a 390px viewport
   on every Studio screen. Both fixed; the audit measures the rendered page, so the
   `aria-`-counting method in `ITERATION_CHANGES_PHASE3.md` is superseded rather than
   extended.
10. **The delivery manifest did not describe the delivery.** `SOURCE_MANIFEST.sha256` is
    tracked and cited as release evidence but nothing validated it: measured with the
    checker's own rule against the last commit before this work (`e1a8957`), it listed 154
    rows against 188 tracked source files (migrations 007–012 among the 34 absent) and 44
    of those rows no longer matched their content — 110 of 188 files truthfully represented,
    while the file read like an integrity guarantee. `release-evidence.sh` was already
    generating a correct `source.sha256` per run, a second and fresher authority over the
    same fact. `scripts/source-manifest.sh {write,check}` now exists, `static-verify.sh`
    runs `check`, and the control arms (missing entry, flipped hex, zero enumerated files,
    freshly written manifest, `./path` compatibility) were exercised in a scratch repository.
    The gate then found a bug in its own filter — an off-by-one prefix comparison let
    `release-evidence/` through, which showed up as `stale=6` the moment verdict files were
    committed — so the figures above are the recount *after* that fix, not the first reading
    (which said 194/40/112 and is now wrong in the message of commit `28deafc`).
11. **The subject export 500s on one table and silently empties two others.** Found by
    building the erasure drill and running it, not by reading the code:
    `PERSONAL_TABLES` was derived from the migrations' `REFERENCES users(id)` columns (correct)
    but queried with a fixed `WHERE workspace_id=…`, and `brand_submissions` is tenant-scoped by
    `submitting_workspace_id` instead (`db/migrations/002_creation_asset_market_os.sql:496`) —
    `column "workspace_id" does not exist` took down the whole endpoint. With that fixed, the
    response was a 200 whose `preferences` key was `[]` while the row existed, because
    `user_preferences` and `product_events` are `FORCE ROW LEVEL SECURITY` tables and an
    unscoped read returns nothing and raises nothing — the same RLS trap that produced defects
    #1 and #5, this time on the read side and invisible to a status code. Both fixed: the list
    is now `(table, actor column, tenant column)` triples, and every RLS-guarded store is read
    under each of the caller's own memberships.
12. **Erasure could not run at all: it tripped its own session-immutability trigger.** The 013
    function revoked sessions with `expires_at=now()` alongside `revoked_at`, and 005's
    `guard_auth_session_update` (`db/migrations/005_final_release.sql:44-54`) lists `expires_at`
    among the immutable columns — so every `POST /api/account/erasure` returned 409
    `immutable auth session fields cannot change` with the account untouched. `db/migrations/014`
    drops that one clause; revocation is what the auth path enforces (`auth.py:123/150/178`) and
    what the app's own rotation and logout already write (`main.py:177`, `main.py:188`). The drill
    now asserts the revoked state *and* that `expires_at` did not move.
13. **A read path nothing exercised.** `product_events` has exactly one writer,
    `POST /api/events` (`services/api/app/routers/creation.py:211`), and no suite had ever
    called it, so the export's event list was an untested branch on an empty table. The drill
    records a `candidate_played` event as its fixture and requires it back in the export.

14. **A terminal job failure could be recorded with no reason at all.** The red run listed 13
   of 15 failed jobs with `error = NULL`: `worker.py` copied only the *provider* payload into
   that column, while the actual cause — `audio decode validation failed: Command '['ffprobe',
   ...]' timed out after 15 seconds` — was written on the `audio_candidates` row by the ingest
   handler, reachable only by a manual join. Attribution of the red itself is measured, not
   assumed: the same archived log shows the checkpoint above and shows a single-row read of
   `generation_jobs` exceeding the 15 s `DB_STATEMENT_TIMEOUT_MS` (`main` sets it in
   `services/api/app/settings.py:50`, `services/worker/worker.py:63` passes it for the worker),
   i.e. the I/O subsystem stalled rather than any query being pathological. **No timeout was
   loosened**; `services/worker/outcomes.py::terminal_error` now composes the reason at
   settlement (provider payload when present, otherwise a code derived from the counters, plus
   up to three per-candidate causes), verified live on the rebuilt stack — a `partial_success`
   job reports `incomplete_candidate_set` with `ready/failed/requested` and the failing ordinal,
   a `failed` job keeps `provider_generation_error` and gains the same. Guarded by 8 unit tests
   scored against the reverted semantics (5 of 8 turn red, real module 8/8 green) and by a new
   live assertion in `provider_regression.py` that fails any job ending
   `partial/failed/dead_letter` without a stored reason (exercised in both polarities).

15. **The repository shipped two provider adapters and no written provider contract, and they
   disagreed.** Turning on `MUSIC_PROVIDER=generic_rest` to check whether the "point it at a
   real adapter" claim actually held produced 422 on *every* submit from our own
   provider-emulator: `EmulatorAdapter` posts a flat `{title, lyrics, styles, bpm, ...}` body
   (`services/worker/provider.py:70-78`) while `GenericRESTAdapter` posts
   `{external_request_id, model, candidate_count, song_spec}` (:193-198), and the emulator
   modelled only the first (`services/provider-emulator/main.py:12-13`). A "contract-first
   adapter" whose contract existed nowhere was the real defect, so the contract is now a file
   (`shared/contracts/provider-submit-v1.schema.json`), the emulator accepts and *labels* the
   dialect it received, the adapter exposes `submit_payload()` so its body is checked against
   the schema by the unit suite rather than by a copy in a test, and the acceptance contract
   gains the server-side case. A must-fire arm is kept on both sides: the flat native body is
   asserted NOT to satisfy the vendor schema, and a body without `external_request_id`, with
   `candidate_count` 0 or 9, with a title-less `song_spec`, an out-of-range `bpm` or an
   undeclared key is rejected.

   Recorded alongside it, because it wasted a run: **a compose command that omits an overlay
   file can silently revert services it did not touch.** `docker compose --profile test run
   --rm acceptance` reconciles its `depends_on` services (api, worker) from the file set named
   on *its own* command line, so running it after an overlay `up` brought the containers back
   with `MUSIC_PROVIDER=emulator` while the overlay file still looked applied. That is why the
   round-trip step does `up` and the batch with nothing in between, and why the batch verifies
   the identity before submitting anything.

## Not verified: the 500-user capacity gate


`scripts/capacity-gate-500.sh` was executed for real and **FAILS on this host**: at the
configured 500 users the gate needs p95 ≤ 800 ms and measured p95 **4197 ms** on one run and
**9832 ms** on another, with `error_rate=0.000%` and every request returning 200.

Concurrency sweep on the same endpoint (`GET /api/projects` through the gateway, 5 requests
per user unless noted):

| users | p50 (ms) | p95 (ms) | gate |
|---|---|---|---|
| 1 | 2.0 | 2.6 | PASS |
| 10 | 36.0 | 48.5 | PASS |
| 20 | 91.3 | 115.9 | PASS |
| 25 | 138.0 | 246.0 | PASS |
| 30 | 120.6 | 180.3 | PASS |
| 50 | 617.3 | 1004.4 | FAIL |
| 100 | 1379.3 | 1896.4 | FAIL |
| 200 | 1697.2 | 1951.4 | FAIL |
| 400 | 2426.1 | 3308.9 | FAIL |
| 500 | 5374.4 | 9832.3 | FAIL |

Interpretation, stated as evidence rather than as a pass: a single request costs ~2 ms and
the curve is near-linear in concurrency, so the endpoint is not doing anything pathological;
throughput stops rising above ~130-160 req/s and latency is queueing. `docker-compose.capacity500.yml`
itself requests 4 CPU/4 GiB for the API and 4 CPU/4 GiB for the Worker, and this VM has 4
vCPU / 6 GiB total — below `COST_OPTIMIZED_500_CONCURRENCY.md`'s own recommended 4 vCPU /
16 GiB host. **No parameter change is justified by this data, and the 500-user claim remains
unproven**; it needs a host matching the profile (or CI's larger runner).

Also noted for the owner, deliberately not "fixed" by loosening a control — and
**correcting what an earlier revision of this file asserted**: `LOGIN_RATE_LIMIT_PER_MINUTE`
(default 10) is keyed on `login:sha256(email)`, i.e. **per account, not per IP**, so the
worried-about "users behind one egress IP throttling each other" scenario does not exist
here. Measured against the running API instead: the 11th attempt to one address is refused,
and because the throttle script calls `EXPIRE 60` on **every** increment — including the ones
it refuses — a client that keeps knocking every 30 s stayed locked for 90 s and only
recovered after 75 s of silence. So the control neither does what its name implies
("per minute" is a sliding window that the traffic itself extends) nor bounds distributed
credential stuffing across many accounts, while anyone who knows one address can keep that
account unable to sign in. The one-line remedy (`EXPIRE` only when the counter is created,
making it a true fixed window) is an authentication-side security change and is left to the
owner rather than applied here; what was fixed is the test harness, which had been retrying
inside the window and could therefore be starved by its own retries.

## Scope of the external-tooling review, and what was skipped

Per project practice, new or large components are compared against mature implementations
before being built. Three such comparisons were made this round, all from material actually
retrieved in this session; retrieval limits are named rather than papered over.

### Load generator (earlier this day)

Recorded with sources in `COST_OPTIMIZED_500_CONCURRENCY.md`. Verdict: keep the in-repo
tester; the shortfall measured was host capacity, not tooling.

### Browser accessibility gate (this commit)

Metadata for five candidates was fetched from `registry.npmjs.org` (version, licence, last
modified): axe-core 4.13.0 MPL-2.0 (2026-09-23), pa11y 10.0.0 LGPL-3.0-only (2026-08-28),
Lighthouse 13.5.0 Apache-2.0 (2026-09-19), Playwright 1.63.0 Apache-2.0 (2026-09-25),
@axe-core/playwright 4.13.0 MPL-2.0 (2026-09-02). GitHub page fetches timed out in this
environment, so no repository README is claimed as read for axe-core or Lighthouse; the
pa11y README **was** read, from its npm tarball.

| candidate | fit | licence | activity | risk | quality | adaptation cost |
|---|---|---|---|---|---|---|
| Playwright + axe-core (chosen) | one authenticated session walks 15 SPA views, phone viewport, and the non-axe assertions this gate needs (CSP-blocked inline styles, computed-style settle, keyboard reach, overflow) | Apache-2.0 + MPL-2.0 | both current | test-only; axe fetched by pinned sha256 | first-party browser driver | new node-free dep in the existing Python drill convention |
| pa11y / pa11y-ci | one URL per run; its `actions` do support form login (`set field #username …`, `click element #submit` — README example), but each tested view is a fresh invocation | LGPL-3.0-only | current | 15 invocations ≈ 15 logins, which runs straight into the measured per-account throttle (10/min, sliding) | mature, wraps axe + htmlcs | lowest: config file + one command |
| Lighthouse | performance/PWA categories this gate does not assert; scripted navigation between authenticated views is not its shape | Apache-2.0 | current | heavier output than needed | axe under the hood | medium |

**Correction, stated plainly because it is in the git log:** the message of commit
`f2d8c38` asserts pa11y "authenticates with headers, not a login form, so it would only
ever see the sign-in screen". Reading its README showed that claim to be false — form
interaction is documented. The choice of Playwright stands, but for the reasons in the row
above (one session covering fifteen views without paying the login throttle; assertions
that are not axe's job), not for the one in that commit message.

### Subject access export and right to erasure (this commit)

Candidates looked at through the GitHub search API in this session: **Ethyca Fides**
(`ethyca/fides`, "The Privacy Engineering & Compliance Framework", 484 stars) and its DSAR
predecessor (`ethyca/fidesops`, "Privacy as Code for DSAR Orchestration: Privacy Request
automation to fulfill GDPR, CCPA, and LGPD data subject requests", 49 stars) — **both now
`archived: true`** (fides last touched 2026-09-22); **PostgreSQL Anonymizer**, whose ecosystem
was read indirectly through `CuriousLearner/django-postgres-anonymizer` (29 stars, 2025-09) and
`sendtoshailesh/aws-rds-data-masking-data-anonymization`, whose own description states the
extension "is not available/compatible with Amazon Aurora or RDS PostgreSQL instances, where
custom extensions cannot be installed"; and `erichard/awesome-gdpr` (539 stars, updated
2026-09-24) as the directory. What was **not** read: fides' or the anonymizer's README or docs —
GitHub page fetches time out in this environment (same limitation noted for axe-core above), so
the internals of both are described here only as far as their metadata and descriptions say, and
the load-bearing fact is the archived status, not a code reading.

| candidate | fit | licence | activity | risk | quality | adaptation cost |
|---|---|---|---|---|---|---|
| In-repo endpoint + `SECURITY DEFINER` migration function (chosen) | the two rights are one transaction each against **this** schema: 19 user-linked stores, RLS-protected, plus the guards this platform needs (sole owner, platform admin, confirmation-by-email) | n/a, own code | n/a | authority write isolated in a reviewed function, the pattern 011/012 already establish; every guard asserted on both polarities by `erasure_drill.py` | 34 resident checks, coverage re-probed against `information_schema` | lowest available: no new runtime dependency, follows 011/012 |
| Ethyca Fides / fidesops | purpose-built DSAR orchestration across many external systems (connectors, policy engine, request state machine) — far more than one Postgres cluster needs | per its repo (not re-read this session) | **archived**, which ends "adopt a mature implementation" as an option | would add a second service, its own store of subject data, and its own auth path to a stack whose RLS is the security model | large, mature codebase | highest: multi-service deployment for one endpoint pair |
| PostgreSQL Anonymizer | rule-based column masking / static anonymisation of a whole table or a role's view — a different axis from "erase one subject, keep the books" | open extension | maintained (integration repos current into 2025-26) | managed-Postgres targets cannot install extensions; this stack runs stock `postgres:16-alpine` (`docker-compose.yml:5`) | purpose-built for its own problem | a custom base image plus `shared_preload_libraries`, and still no export half |

**What was borrowed:** the anonymizer's declarative-rules idea became the export's `coverage`
declaration (the payload states which `table.column` stores it reads, and the drill subtracts
every `users(id)` foreign key in the live schema from it), and Fides' "a privacy request leaves
an audit trail" idea became the `privacy.account.erase` audit row written in the same
transaction as the erasure itself.

**What was deliberately not surveyed**: the remaining changes are scope-clear local fixes
reusing a pattern already in this repository — the reservation and refund guards follow
`reserve_marketplace_offer`'s existing `SECURITY DEFINER` shape (application-side writes are
what silently matched zero rows); apt-over-TLS, the two host-port variables and the
`static-verify.sh` directory pruning are environment robustness with no design surface; the
cross-tenant, contention, race, reconciliation and regression suites are tests built on the
existing acceptance/drill harnesses and httpx/psql conventions; and the manifest checker
mirrors `release-evidence.sh`'s existing `source.sha256` rather than inventing a second
scheme.

## Requires external commercial evidence


A real music provider, payment processor, tax/fiscal setup, identity provider and legal
approval cannot be embedded in source code. The default system remains fully usable with
deterministic local AI fallback, Provider Emulator and Payment Emulator. Commercial rights
remain blocked until an approved provider capability snapshot and rights evidence are
configured — and that block was itself observed working: the isolation suite's commercial
offer is refused on the default stack with
"provider contract does not permit commercial capabilities; legal review cannot override
provider rights", which is why the licence/delivery/payout half of the tests runs under the
commercial overlay.

## Cost-optimized 500-user hardening addendum

代码侧容量强化已经合入：API DB pooling/backpressure、并行 Worker、HTTP keep-alive、对象存储
presigned delivery、容量索引、Kubernetes HPA/PDB 与自动 500-user capacity gate。跨容器 E2E 与
100 次生成回归**本轮已在真实 Compose 栈上执行并留证**（见上）；500-user runtime evidence 仍
**未取得**——本机 4 vCPU / 6 GiB 低于该 profile 自己声明的资源请求，Gate 实测判红，须在达标主机
或 CI 大规格 runner 上重跑，不能用静态验证或小规模通过率替代。

## 2026-09-26 second factor, membership writes, and a deployment that cannot inherit repo secrets

Three gate items closed in one round, and the second factor is the reason the other two turned up.

**Second factor (TOTP).** A password now stops at a pending credential (`purpose=mfa_pending`,
`amr=["pwd"]`, no session row and no cookies); `POST /api/auth/mfa/challenge` is what mints the
session. Enrolment is staged -- the seed is stored sealed and pending, and only proof of a current
code arms it -- so an abandoned enrolment cannot lock anyone out, and a seed left unconfirmed past
the window is void. The replay guard (`accept_mfa_step`: a strictly greater time step, per account)
and the single-use recovery codes live in `db/migrations/015` rather than in process memory, and
`auth_sessions` gained `amr`/`mfa_at`, which refresh rotation carries forward. `get_user` refuses a
session that never presented the factor once the account is armed, and arming stamps the session that
proved the code so enrolling cannot lock out the person enrolling. Codes are RFC 6238 through pyotp
2.10.0, verified against all eighteen published Appendix B vectors *and* against an independent
stdlib implementation that the tests carry (the widely quoted SHA1 value for T=59 is 94287082; the
94290855 often cited for that row is not what the standard's own key and time produce, and the vector
table in this repository was wrong about it until it was re-derived with a third implementation).
Seeds are AESGCM-sealed with a key that has no in-file default. `scripts/mfa_drill.py` walks 52
assertions on the live stack; four of them matter most: a pending token presented at an
access-token endpoint is refused, an accepted code cannot be replayed on a *different* pending token,
an account armed after a session opened leaves that older session refused, and a re-enrolment
invalidates the previous recovery set at the hash level, not just in behaviour.

**What adding it exposed.** Two privacy defects, both now fixed and both given teeth that a future
column cannot slip past:

* `erase_user_identity` did not clear the six new `users` columns, so an erased subject who had armed
  a factor kept a seal-openable authenticator seed and ten recovery-code hashes in the erased row.
  The erasure drill could not see it because it predates 015 and its coverage assertion enumerates
  *foreign keys pointing at* `users`, not the columns `users` gained. 016 clears them; the drill now
  derives "which nullable columns must be NULL afterwards" from `information_schema`, seeds a
  type-appropriate value into each one before erasing (so "NULL after" cannot mean "always was"), and
  hands its classifier a row the database would never produce to prove it reports a leftover.
* The subject-access response selected seven named columns and its `coverage` list was only compared
  against those same foreign keys, so it said nothing about the authentication columns or about
  `password_hash`. It now names every column it reads and declares what it deliberately excludes,
  with the reason in the payload.

**Membership writes (G11).** The API had always been able to read `workspace_members` and never to
write it -- `music_app` has SELECT only, so an application-side UPDATE matches zero rows and raises
nothing, the shape 011 and 012 exist because of. Migration 017 carries add/change/remove/transfer as
`SECURITY DEFINER` functions; the last-owner rule is the same predicate the eraser uses, transfer
promotes before it demotes so no intermediate state is ownerless -- and, contrary to what this file said when
the migration shipped, the legal role names *are* restated: `workspace_role_error()` spells all eight, and every
function calls it first, so that second list is what decides legality today. The roster work made that load-bearing
(see the round below), and the fix was a resident three-way agreement check rather than a plpgsql rewrite -- 017 is
applied, and an applied migration's checksum is frozen. This also removes a dead end that had shipped twice:
since 014 the eraser has refused a sole owner with the instruction "transfer ownership first", and
until now nothing in the repository could do that. `scripts/member_drill.py` (33 assertions) includes
two that issue a raw `INSERT` and a self-promoting `UPDATE` as `music_app` and require the database to
answer `permission denied`, one that calls the function with a forged actor, and the loop that closes
the dead end: sole owner -> erasure refused -> transfer -> erasure succeeds.

**Secret hygiene.** `docker-compose.yml` restated the same literals as `app/settings.py`
(`${JWT_SECRET:-local-development-...}`) and `docker-compose.production.yml` overrode none of them, so
a production deploy that forgot a variable booted on a signing key published in a public repository.
The four signing secrets now interpolate with `:?` only, and `DEMO_STACK` -- pinned to `"true"` as a
literal in the base file, `"false"` in the production overlay, deliberately not `${DEMO_STACK:-true}`
so a stray host `.env` cannot flip the licence -- makes the API refuse to start otherwise; observed
live: `refusing to start: jwt_secret is still a literal published in this repository | ...`.
`.env.example` had a second trap that CI copies verbatim: `JWT_SECRET` and `MEDIA_SIGNING_SECRET` were
both `change-me-before-sharing`, i.e. whoever could mint a media link could mint a session.

Two gates keep those properties from rotting. `tests/unit/test_secret_defaults.py` (16) checks the
policy in both directions and sweeps every compose file for the `${NAME:-literal}` shape.
`tests/unit/test_environment_contract.py` (9) derives the configuration contract from three grammars
-- python including this repo's own `csv()`/`boolean()` wrappers, compose mapping keys *and* `${}`
interpolations, Dockerfile `ENV`/`ARG` counted as a provider and not as a reader -- and diffs both
directions. Its measurements: 83 names read by code, 13 provided-but-not-read names excused by
attributing each to a real other consumer (the postgres/MinIO/redis images, uvicorn's own flags,
host-port mappings), and one dead variable found and deleted -- `WEBHOOK_SECRET` on the
provider-emulator, whose code never pushes a webhook, so the line made the provider secret look wired
when it was not. Every allow-list entry is re-checked for still being true, which is how two of my own
first guesses (`API_BASE_URL`, a documented `GENERIC_PROVIDER_AUTH_PREFIX`) were rejected: the first is
provided after all, and the second's default is `"Bearer "` with a trailing space that no `.env` line
can be relied on to keep.

**Browser surface.** The login second step, the pending enrolment panel, the recovery-code list and
the armed panel are audited by `scripts/browser_a11y.py` in both viewports on a probe account the gate
creates and deletes, because arming a demo account would break every other drill that logs in with a
password alone. The report carries `second_factor_states` so a probe that failed to provision shows up
as an empty list rather than as a silent skip. The four new states add no new finding type: the armed
panel and the plain account view report the same moderate rules with the same node counts. (That sentence was
true of the tree it was written against; the round at the end of this file both widened it and lowered the
numbers, because part of what the two views used to share was the login screen rendering where it should not.)

## 2026-09-26 the privacy endpoints get a surface a subject can actually walk

`GET /api/account/export` and `POST /api/account/erasure` had been shipped, drilled 46/46 (53/53 as of the last
round) and unit-guarded,
and none of it was reachable from the product: only the second factor had a panel. A right that needs a
terminal is not delivered to the person it belongs to, so this round is the interface plus the walk that
proves the interface.

**How the export should be delivered (research, then a decision).** The mature pattern in large platforms is
an *asynchronous* archive: GitHub's own settings flow is "click Settings, then Account, then Start export",
the archive is built in the background, and a download link arrives by e-mail and expires in seven days
(<https://docs.github.com/en/get-started/archiving-your-github-personal-account-and-public-repositories/requesting-an-archive-of-your-personal-accounts-data>,
read this round). I looked for GitLab's equivalent page and reached `docs.gitlab.com/user/account_settings/download_data/`,
which redirected into a login/state flow before any text was readable, so I am citing it as located-but-not-read
and not as a second source.

- Candidate A, asynchronous archive + e-mailed link: matches what the biggest products do, tolerates an
  arbitrarily large account, and costs a job queue, a store for the rendered bundle, a mail channel, and a
  link-lifetime policy.
- Candidate B, synchronous download of the JSON the API already returns: one click, no new moving parts, and
  the bundle is already per-column declared (`coverage`) with its exclusions named and reasoned.
  Six-month-old exports of a workspace-scoped account are ~2.6 KB here (measured in the walk).

Decision: **B**, and not on taste -- a grep for any mail transport in this repository (`smtp`, `sendgrid`,
`mailgun`, `ses`, `boto3`, `nodemailer`) returns no consumer at all, so A would mean standing up an e-mail
pipeline that this delivery has never had, to move a payload that fits in one response. The reuse that did
apply was in-repo: the panel is built from the app's existing conventions (the same `api()` helper with its
CSRF handling, the same blob-download path `downloadAsset` uses, `escapeHtml`/`textContent`-only rendering,
`button.danger`, `role="alert"` regions). If a real deployment ever grows a mail channel and accounts that
cannot fit in one response, A becomes the right shape and the endpoint boundary does not move.

**What the panel says, not just what it does.** The export button renders the response's own declarations
after downloading: how many `table.column` stores were covered, and the *excluded* fields with the reason
that came from the server (`password_hash` is a credential digest, not data about the subject). The erasure
block states in the interface what the database keeps -- sessions revoked, memberships and preferences
removed, identity columns pseudonymised, while accounting and provenance rows survive under a
non-identifying actor id, because `song_projects` etc. hold NOT NULL foreign keys and `audit_events` /
`song_spec_revisions` carry append-only triggers. The confirmation is the account's own e-mail, matching
`013`'s predicate; the client-side unlock is convenience, the database refusal is the guarantee, and the
409 leaves the session alive so a person can read why and act on it.

**Design note (was unresolved, now closed).** Nothing in this product used to require re-authenticating
immediately before self-erasure -- an XSRF-drained session cookie plus a typed e-mail is weaker than the
"step-up" flow most account-deletion products use. Raising it meant a threat-model decision about what a stolen
cookie can do, so it was left as an explicit open item rather than smuggled in beside a UI change. The following
round decided it and implemented it; see *the destructive writes stop trusting the session cookie* at the end of
this file.

**Two console-hygiene defects the walk surfaced, and one fixture hazard.** `/api/auth/me` answering 401 on a
cold load is the normal signed-out state, yet both apps logged it as a fault; gate console entries went
16 -> 8 and the remaining 8 are the browser's own network log, which app code cannot suppress.
`admin.js`'s `api()` did not attach the response status to the error it threw, so a status-based guard there
was not merely missing but unwritable -- the wiring is pinned by a test that fails when the assignment is
removed. And because cookies are host-scoped rather than port-scoped, the session cookie the erased probe left
behind was handed to the admin origin in the same browser context, turning the admin cold load's 401 into
"session has been revoked or expired": real behaviour of the app, but an artifact of running two origins on
one host in one context, and the session cookie is HttpOnly, so only the walk can clear it. The walk does.

**Guards.** `tests/unit/test_privacy_surface.py` (7) checks the panel's paths against the API's own route
table rather than a copied list, pairs the client confirmation guard with the migration predicate, keeps
`innerHTML` out of the privacy renderers, proves every element the handlers select is declared by some
document, pins which regions are `role="alert"` versus `role="status"`, reads the CSRF exemption literal out
of `auth.py` to assert the erasure POST is not in it, and asserts that both apps' 401 guards exist *and* cover
every `console.error` call site. Each criterion was shown to bite on the real tree, not just on fixtures:
eight mutations (renamed download path, loosened confirmation guard, planted `innerHTML`, renamed element id,
dropped `role`, erasure added to the CSRF exemption, guard reverted, `status` assignment removed) all fired,
and the restored tree is green.

## 2026-09-26 the roster gets a panel, and the panel finds a real a11y defect

The membership write path had been shipped, drilled 33/33 and unit-guarded, and still needed curl: an owner could
not add, re-role, remove or hand over anyone from the product. The 团队协作 panel closes that, and it closed nothing
honestly until the browser walked it -- which is how the round's two real findings surfaced.

**Where the role names come from.** A select box needs the legal role names, and three places already hold them: the
CHECK constraint at `001:24`, `workspace_role_error()` inside `017`, and this file's prose. The migration's own
comment claims the functions do not restate the list -- reading `017` again showed they do, exactly, and that the
validator is what refuses first. Rather than add a fourth copy in JavaScript, the API now returns the names parsed
out of `pg_get_constraintdef(workspace_members_role_check)`, a static guard forbids a literal role name anywhere in
that function (proven by planting one), and `member_drill` compares all three representations on every run and posts
one add per name so the vocabulary is shown to be accepted, not merely listed. 33/33 became 39/39, and 47/47 once
the step-up wall described at the end of this file was added. The mismatch
itself is not silently tolerated: an applied migration cannot be edited (checksum), so the guarantee is now the
resident agreement check, and the docs' claim is corrected here rather than left pretty.

**Contrast that axe could never see until a UI enabled it.** `button.danger` painted white on `var(--danger)`
= #ff6b6b -- 2.78:1, serious under WCAG -- in both stylesheets, undetected for every prior run because the only
danger controls in scanned states were disabled and axe skips disabled nodes. The roster put an always-enabled one
in a scanned view and the gate went red on six findings (`browser-a11y-20260926T074554Z`, kept). Both apps now
carry `--danger-solid: #b3261e` (6.54:1 measured) for filled danger controls, `--danger` stays a foreground token,
and the ratio is computed from the stylesheets by a resident test -- palette drift is caught without waiting for
some future panel to expose it.

**Fixture hygiene.** Two walks that aborted mid-run left three probe accounts in the demo workspace roster, because
cleanup sat on a line after the browser loop. Cleanup is now registered at creation via `atexit`, per-table and
tolerant, and the hook's reachability was proven rather than assumed: a control that raises `SystemExit` confirms
the callback still runs. The roster walk itself refuses to click unless the panel is displaying the probe's own
e-mail as the erasure/ownership target, which is what makes "the gate cannot delete the demo account" a property of
the code rather than of care.

**Readings from the authoritative run** (`acceptance-20260926T075659Z`, commit `93b4984`): 19 steps PASS + capacity
skipped; 189 unit tests; member drill 39/39; erasure 46/46; mfa 52/52; reconciliation 15/15; 100-run regression
p50 6.84s / p95 8.21s; generic-REST 25/25 at p50 5.14s / p95 6.34s; browser 88 view records over 62 axe scans,
critical 0 / serious 0, 96 moderate (`region` 56, `heading-order` 32, `landmark-one-main` 8), zero uncaught
exceptions, 10 console entries all from the network layer (8 expected 401s, 2 expected 409s from the roster
refusal), and the two walks reporting `2 exports downloaded and parsed, 2 probe accounts erased` and
`2 roster walks with add/re-role/remove exercised`.

## 2026-09-26 who-can-do-what becomes a measurement instead of folklore

G12's approval-flow row asks for a written answer to "who approves what". Half of that is not a
business decision at all: it is already in the code, as the role set each mutating endpoint demands.
`scripts/authority_matrix.py` reads the route decorators and dependency signatures with an AST and
emits two paired artefacts -- `docs/AUTHORITY_MATRIX.md` for humans and
`shared/contracts/authority-matrix.json` for machines -- bucketing 89 `/api` routes (48 writes) by how
each learns who is calling: an explicit `require_roles` list, platform admin, any workspace member, a
live session, or nothing at the door. `static-verify.sh` runs `--check`, which CI already executes, so
removing a role check from an endpoint fails the build rather than quietly contradicting a document.

Writing the reader surfaced two of its own bugs, and the only reason they were caught is that the
matrix is compared against something independent: the running app's own route table. The first version
reported 36 routes because it filtered on the decorator string before prepending the router prefix, so
all 24 router-mounted sub-paths were dropped -- and the total still looked confident. The second blind
spot was visiting only `ast.FunctionDef`, which silently skipped the three `async def` endpoints
(project chat and both webhook receivers). Both are now pinned by fixtures fed to `derive_file`, and
the committed matrix is cross-checked against `app.routes` (89 = 89) in a unit test, so a reader that
goes partially blind fails instead of under-reporting.

The five writes that carry no role check are listed with the carrier that actually decides --
`verify_password`, `validate_refresh_session`, the per-account challenge limiter, and
`hmac.compare_digest` on both webhook receivers -- and the test asserts each marker is still literally
present in that endpoint's body. A new unauthenticated write cannot be added without naming what
protects it: five controls fired (role check downgraded to member, an anonymous write route planted,
the generated document hand-edited, a path renamed in the matrix, a claimed marker removed).

What this does *not* close is the other half of the row: there is still no written sequence across the
approval surfaces the matrix names (rights review, moderation case, comment resolve, brand-submission
review, payouts, switches) -- no defined order, escalation, timeout or reversal path. That is a process
design to settle with operations, and it stays an open item rather than being renamed as done because a
table now exists.

## 2026-09-26 the destructive writes stop trusting the session cookie

**What was open.** Five writes were reachable with nothing but a live session: erase my account, transfer
workspace ownership, change a member's role, remove a member, and drop my own second factor. The erasure panel
asked the user to retype their e-mail, which proves only that whoever holds the cookie can read the panel -- the
address is printed on it. The checklist had recorded this as an unresolved design question rather than a gap; this
round decided it.

**Research, and what it changed.** Three sources were read, not skimmed: RFC 9470 (the resource server answers
`401 insufficient_user_authentication` and the client repeats the request after obtaining stronger proof), Okta's
step-up guidance (assurance tiers per action, tokens carrying `acr` demanded versus `amr` actually used, cached
sessions preferred), and Laravel's `password.confirm` middleware (verify once, stamp the session, stop asking for
three hours). A Keycloak section was fetched and truncated before the relevant chapter, so nothing here is
attributed to it; GitHub's "sensitive actions" REST page 404'd and the search returned only unrelated hits, so it
was not consulted either. Weighed on fit / licence / activity / risk / quality / adaptation cost, the choice is
**9470's response semantics plus Okta's factor ladder, implemented on this repo's own primitives, and explicitly
not Laravel's window** -- the threat that motivated the item is a stolen cookie, and a confirmation window is the
channel it would ride through. No reusable Python library for this was found; the repo already had the two hard
parts (`verify_password` with `hmac.compare_digest`, and `015`'s `accept_mfa_step` making a code single-use in the
database rather than per worker), so borrowing the shape cost less than adopting a dependency.

**Implementation.** A `StepUp` request model (`password`, `code`, both optional on the schema because "absent"
must be a challenge and not a Pydantic 422) is mixed into the five bodies; each endpoint calls
`step_up_factors()` before it touches the database and records which factors passed in the audit row. Missing
credential answers 401, a presented-and-wrong one answers 403; collapsing them would either let the schema make a
security decision or leave a client unable to tell a first attempt from a brute force. Refusals are written to
`audit_events` against the account itself (not as `system`) in their own transaction. The counter is per account,
counts **failures only**, and a correct answer empties the window -- six guesses a minute, while a manager who
confirms seven roster actions in a minute is working, not guessing. Which routes carry the wall is not prose:
`scripts/authority_matrix.py` walks each endpoint body for the helper call and renders it as the matrix's
"再认证" column, and `--check` runs inside `static-verify.sh`, so CI sees a route that gained or lost it.
`shared/contracts/openapi-v13.{json,yaml}` now document `password`/`code` on exactly those five operations.

**What the drills had to say about it.** `erasure_drill` 46 → **53**: the probe is genuinely armed through the real
endpoints, so erasure owes two credentials, and the drill now proves the ordering -- a wrong password answers 403
and leaves the one-time code *unspent*, which the very next request demonstrates by spending that same code
successfully. Three refusals must appear in `audit_events` with `actor_id` set to the account. `member_drill`
39 → **47**: 401 and 403 both leave the roster byte-identical, the credential is spent before the workspace rule
is consulted (so "transfer ownership first" cannot leak ahead of identity), and the last section fills the
failure window and requires that the *seventh* attempt -- with the correct password -- answers 429.
`mfa_drill` 52 → **54**. One design bug was caught before any pipeline ran: the first version counted attempts,
and the owner account legitimately confirms seven times inside the member drill's minute, so it would have locked
itself out of its own workspace mid-run.

**A server bug the static gates could not see.** `step_up_factors` opened its own transaction with a plain cursor
while `_mfa_pass_code` reads the function result by column name -- a 500 on every armed account's disable. Nothing
in `tests/unit` touches a database, so what caught it was running the three edited drills standalone against the
live stack before launching the twelve-minute chain.

**The defect the new assertion found.** The privacy walk was told to refuse when the panel demanded a code from an
account that has no second factor; on the first run it refused for the opposite reason -- the field was visible.
`label { display: grid }` (both stylesheets) outranks the UA's `[hidden] { display: none }`, so hiding by attribute
had been quietly false for as long as the rule existed. The same failure covered `services/web/app.js:321`
(`$('#login').hidden = true` on an element whose class sets `display: grid`), which means **the login screen kept
rendering above the app on every signed-in view**: the before/after screenshots of the same state are in the two
evidence directories, and the 15 committed browser reports show `region` violation nodes growing almost 1:1 with
scan count (62 scans / 180 nodes, 88 / 324, 96 / 348) -- every view was being audited with a login form in it.
Fix in two parts: `[hidden] { display: none !important }` in both stylesheets, and a page-wide sweep in the gate
that fails if any element marked `hidden` still computes a display, with a denominator assertion so an empty sweep
cannot read as clean (this round: 840 elements marked hidden, 0 still rendering). The follow-up item that used to
read "96 moderate, `region` 56" is now "52 moderate, `region` 8", and the difference is this defect, not new work
on landmarks.

**Residuals, stated rather than hidden.** Adding a member stays credential-free -- it is additive and reversible,
and the boundary this round drew is "irreversible or privilege-removing". An account that has lost both its phone
and every recovery code cannot reach erasure, but it also cannot log in to ask, so the ladder does not create the
lockout. The admin console still has no member view. And a per-action password means an armed account types a code
per destructive action -- a deliberate trade against the three-hour window that was rejected above.

**Readings from the authoritative run** (`acceptance-20260926T095844Z`, commit `1f19952`, fresh database created by
`down -v` before start, 09:58:44Z → 10:16:39Z): 19 steps PASS + capacity skipped across 20 rows; 210 unit tests;
`authority matrix agrees with the code: 89 routes, 48 writes` with 5 step-up routes; acceptance 11 passed twice;
contract 5; chaos; lease 8 jobs / 2 workers / 160 credits once; fidelity 2 workspaces and 2 assets byte-identical
with ledgers unchanged; erasure 53/53; mfa 54/54; member 47/47; reconciliation 15/15; 100-run provider regression
100/100 at p50 6.35s / p95 7.9s settling 1000 credits; generic-REST 25/25 at p50 5.17s / p95 6.31s settling 250;
browser 96 view records over 70 axe scans with critical 0 / serious 0 and 52 moderate, zero uncaught errors, 12
console entries (8 expected 401 network lines, 2 expected 409 from the roster refusal, 2 expected 403 from the
wrong-password attempt), 4 privacy states / 8 roster states / 5 second-factor states, 2 exports of 2614 and 2613
bytes and 2 probe accounts erased. The run that found the `[hidden]` defect is kept as a discovery record
(`acceptance-20260926T093142Z`, step 19 FAIL, with `browser-a11y-20260926T094118Z/report.json`).
