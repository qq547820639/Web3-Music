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
`b79e70b`, 2026-09-26T02:44:13Z → 2026-09-26T02:55:36Z, evidence in
`release-evidence/acceptance-20260926T024413Z/` (per-step logs, `SUMMARY.txt`, full
`compose-logs.txt` and `commercial-compose-logs.txt`), with the browser audit's own
machine-readable record at `release-evidence/browser-a11y-20260926T025229Z/report.json` (stamped with the same commit).
What is version-controlled from that directory is only `SUMMARY.txt` (per-step verdicts and timestamps) and the
browser run's `report.json`; the per-step logs and the two compose log files stay on the host that ran the pipeline.
So a clone can re-check the step ledger and the whole browser reading (views, scans, violations per impact, CSP,
`uncaught_errors`, the second-factor states) against committed artifacts, while the drill tallies and timings quoted
below come from per-step logs that exist only on that host -- re-running the pipeline is how a reader verifies those.
The pipeline is 20 rows wide now: `mfa-drill` joined at step 12 and `member-drill` at step 13, which
is also where the erasure drill's step 11 reading comes from.
Seven green runs precede it on this host
(`82f2ffe`, `5028686`, `28deafc`, `1d8534e`, `01e61d1`, `2c3ef7f`, `96d5955` — older → newer, at
15/15/16/16/17/18/19 rows with `FAIL=0` in every `SUMMARY.txt`), so the pass is reproducible rather
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
  Across all eight green runs, in run order: **p50 8.18s / p95 20.77s**, **p50 7.615s / p95 11.37s**,
  **p50 6.25s / p95 8.31s**, **p50 8.525s / p95 13.64s**, **p50 5.195s / p95 9.89s**,
  **p50 7.18s / p95 15.66s**, **p50 5.09s / p95 5.37s** and **p50 4.09s / p95 5.33s** (the
  authoritative run, step 17) -- every one of them `100/100 completed, error rate 0.0%` with 1000
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
  `job_error={'type': 'ConnectError', ...}`). The step has run three times and read `25/25 completed,
  error rate 0.0%, settled 250 credits` every time (p50/p95 5.21s/6.37s at step 16 of `2c3ef7f`,
  4.14s/4.19s at step 17 of `96d5955`, 4.14s/4.18s at step 18 of the authoritative run), so the count
  is the reading that travels and the timing is not. This is the adapter and contract plumbing, not
  a provider: the endpoint behind it is still our emulator, so the gate for 100 *real*
  provider runs stays open.
- **Real-browser walkthrough + axe audit** (new): Playwright driving axe-core 4.13.0
  (pinned by sha256, fetched at run time, test-only) against the live stack —
  **70 view records, 44 axe scans across desktop 1440 and phone 390, 0 critical and 0
  serious**, 64 moderate left as follow-up (`region` 40, `heading-order` 18, `landmark-one-main` 6).
  It also asserts what axe cannot see: the shipped CSP must block **zero** inline styles
  authored by the app (measured 0 after the fix, 10 per radar render before), no uncaught
  exceptions, `script-src 'self'` present on both origins, skip-link and keyboard access
  to the nav, and no horizontal overflow at 390px.
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
- **Second-factor drill** (new, step 12): `scripts/mfa_drill.py` — `52/52 checks` in the authoritative
  run, and the codes come from `scripts/e2e_client.py`'s own stdlib RFC 6238 implementation rather than
  from the pyotp the server uses, so a green run cannot be two halves of the same mistake. The four
  assertions that carry weight are listed in the round section below; the drill also waits out the
  fixed challenge window by reading the Redis key TTL instead of sleeping a guess.
- **Workspace membership drill** (new, step 13): `scripts/member_drill.py` — `33/33 checks`, including
  two that issue a raw `INSERT` and a self-promoting `UPDATE` as the application role and require the
  database to answer `permission denied`, and the loop that closes the erasure dead end: sole owner →
  erasure refused → transfer → erasure succeeds.
- Static verification and unit tests: 176 unit tests (114 at the previous authoritative run `2c3ef7f`; this file had recorded 23, then 54, 66, 70 and 78 in earlier revisions), of which 36 arrived with the provider contract: 26 on the generic
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
promotes before it demotes so no intermediate state is ownerless, and the legal role names are not
restated but left to the `CHECK` constraint. This also removes a dead end that had shipped twice:
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
panel and the plain account view report the same two moderate rules with the same node counts.
