# Resonance v13 Final Release Status

Updated 2026-09-26; first written 2026-09-25 after the first end-to-end execution of the
cross-container suite in this delivery environment. Earlier revisions of this file stated that the environment
"does not expose a Docker daemon" and that the Compose E2E had to be run elsewhere; that
was true of the packaging environment but not of this one, and it had stood in place of
evidence for every gate in `RELEASE_CHECKLIST.md`. The gates are now recorded against what
was actually executed.

## Executed on a real Compose stack

Authoritative run: `scripts/acceptance-all.sh` on a fresh database (`FRESH=1`, which is this round's way of making the sentence mean something -- the pipeline brings the volumes down itself and records `fresh_database=1` in the header of the same file), **22 steps PASS and 1 recorded as skipped** across 23 rows, commit `680a0ea`, 2026-09-29T02:26:10Z → 2026-09-29T02:49:48Z, evidence in `release-evidence/acceptance-20260929T022610Z/`, started under `host_load="5.75 5.52 5.54"` on a Docker VM 4 vCPU with `disk_free_kb=9204064` recorded beside it, with 36 prior green runs on this host (`82f2ffe`, `5028686`, `28deafc`, `1d8534e`, `01e61d1`, `2c3ef7f`, `96d5955`, `b79e70b`, `3da3920`, `93b4984`, `99d5847`, `1f19952`, `414752d`, `aa3605b`, `fc70d13`, `cb8b901`, `cc63bee`, `2f25fe5`, `037b818`, `ef88d14`, `3dbd175`, `1dbf99e`, `fb6ff39`, `a841628`, `d98e650`, `c9e3cbd`, `c007b21`, `c469bf3`, `e75dcc6`, `e699e60`, `01147cf`, `8f69a1e`, `fa64cef`, `09be2ab`, `16e2660`, `83f19ad` — older → newer, at 15/15/16/16/17/18/19/20/20/20/20/20/20/20/20/21/21/21/22/23/23/23/23/23/23/23/23/23/23/23/23/23/23/23/23/23 rows with `FAIL=0` in every `SUMMARY.txt`), and 27 judged-red SUMMARYs kept as findings (26-09-25 = 7, 26-09-26 = 8, 26-09-27 = 5, 26-09-28 = 7).
`414752d` was this file's authority until the round before last, and the reason the sentence is now machine-checked rather than maintained: the paragraph below it carried a count, a commit list, a row list and a red total that nothing compared against the archive, so the header could describe a run that was no longer the newest one for two full rounds before anyone noticed.
The browser audit's own machine-readable record for the certified run is at `release-evidence/browser-a11y-20260929T024252Z/report.json`, stamped with the same commit `680a0ea`: the code was committed *before* the authoritative run was started, so the `git_commit` in the record is the tree that was actually tested rather than HEAD-plus-staged-changes. Both halves of that sentence are stamped cells now, and the pairing they rest on was repaired this round: the reader used to take whatever `browser-a11y-*/report.json` was newest *on the same calendar day*, which on 2026-09-28 -- the day carried several a11y legs, each belonging to a
different run -- six by the close of that day (`070328Z`→`065139Z`, `083139Z`→`081806Z`, `085606Z`→`084412Z`, `100927Z`→`095607Z`, `105051Z`→`103425Z`, `113020Z`→`111210Z`; each pairing is a measured row of
`browser_pair` over the tracked archive) -- paired a certified run with another run's report and quietly moved
the hidden-element census by one. It now requires both keys -- the report's `git_commit` equals the one the SUMMARY records, and its `generated_at` falls inside that run's own `browser-a11y` row window -- and when nothing satisfies both, the answer is "no report" and the round refuses rather than quoting the closest file. The commit-equality is enforced in `derive()` for the same reason: the sentence claims "the same commit", so a run whose a11y leg tested a different tree cannot be stamped at all.

What is version-controlled from that directory is three things: `SUMMARY.txt` (per-step verdicts and
timestamps), the browser run's `report.json`, and `server-refusals.txt` -- the census of 4xx answers read off
`docker compose logs api` over that run's own `browser-a11y` row window, written as the tail of step 22 and by
CI's browser job alike. The per-step logs and the two compose log files stay on the host that ran the pipeline.
So a clone can re-check the step ledger and the whole browser reading (views, scans, violations per impact, CSP,
`uncaught_errors`, the second-factor states, the per-endpoint refusal counts) against committed artifacts --
including the refusal census without the api log at all (the resident test compares the two committed files,
requiring the server's tuples to be a subset of what the pages saw, and the injected route to exist on one
side only), while the drill tallies and timings quoted
below come from per-step logs that exist only on that host -- re-running the pipeline is how a reader verifies those.
The pipeline is 23 rows wide now: `report-drill` joined at step 15, right after `media-scan-drill`
(step 14), which is also where the erasure drill's step 11 and the member drill's step 13 readings
come from. The pipeline widened as steps were added, so what the green runs share is "each passed
every row that existed then", not "the same 20 rows seven times". The count, the commit sequence, the per-run row counts, the red
total and the per-day split are stated once, in the sentence above, and
`tests/unit/test_release_record_consistency.py` recomputes all five of them from the tracked
`SUMMARY.txt` files -- this file was the fourth face carrying those figures with nothing comparing
them, which is how it kept describing `414752d` as the authority for two rounds after two newer
green runs had been archived.
Five discovery records from this host are kept because each is the evidence for a defect that is now
fixed: `acceptance-20260926T021711Z` stops at step 3 because the acceptance suite signed a provider
webhook with its own copy of the secret literal while the container had never been given the
variable, so a changed deployed value turned it into a 401; `acceptance-20260926T022643Z` stops at
step 17 with 98/100 and two candidates whose recorded reason was the unreadable
`{'type': 'ReadError', 'message': ''}`; `acceptance-20260927T030055Z` stops at step 6 because the
chain rebuilt only what `docker compose config --services` reports, and that listing omits
profile-gated services, so `worker-b` was still running an image hours old -- the step failed with a
message about leases for what was really a stale build; `acceptance-20260927T035001Z` stops at step 5
with "expired lease was not reclaimed by the restarted worker" while that lease had in fact been
reclaimed, by a `worker-b` started by hand 18 minutes earlier and still heartbeating
(`generation_jobs.lease_owner` = `worker-0a906393`, that container's startup identity, against the
killed worker's `worker-e02a9f18`); `acceptance-20260927T065037Z` stops at step 12 with "an unarmed
account still logs in with a password alone -- 500" because the Docker data filesystem had 718780 KB free and
postgres raised `psycopg2.errors.DiskFull: could not extend file "base/18221/18380"` -- a full disk reached the
record wearing a product defect's clothes, so the chain now refuses to start below `MIN_FREE_KB` (4 GiB default)
and logs the reading in the same header.

> **CI status is deliberately not claimed as evidence.** The branch was pushed, which
> dispatches those jobs, but this environment cannot read their outcome: the repository is
> private (unauthenticated fetch returns 404), the `gh` CLI is not logged in, the GitHub MCP
> connector exposes no workflow-run tool, and the local browser has no GitHub session. Treat
> the CI jobs as dispatched-and-unverified. The figures in **this section** were written on 2026-09-26,
> against that round's local run; their producers are the per-step logs, which `.gitignore` keeps off
> the tree, so no cell or test can re-derive them and they must not be read as current. What the
> certified run measures now is in the stamped cells at the head of this file and in the dated
> round sections below.

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
  **p50 6.84s / p95 8.21s** and **p50 5.215s / p95 7.16s** (step 18 of `2f25fe5`'s run) and **p50 4.13s / p95 5.47s** (the authoritative run, step 19) -- every one of them `100/100 completed, error rate 0.0%` with 1000
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
  `3da3920`, 5.14s/6.34s at step 18 of `93b4984`, 4.11s/5.23s at step 18 of `99d5847` and 6.4s/10.17s at step 19 of `2f25fe5`'s run and 4.1s/5.19s at step 20 of the authoritative run), so the
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
- **Second-factor drill** (new, step 12): `scripts/mfa_drill.py` — `56/56 checks` in the authoritative
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
before being built. Four such comparisons are recorded here -- three from that round and one added this round -- each from
material actually retrieved by the session that wrote it; retrieval limits are named rather than papered over.

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

### The login limiter: what "a minute" should mean (this round)

Turning a documented owner-decision into code is an authentication-side change, so it got the same
comparison as the other three. What was actually opened this session, and what each source changed:

- **OWASP Authentication Cheat Sheet** (<https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html>,
  read this round) — the two sentences that decide the shape: *"The counter of failed logins should be
  associated with the account itself, rather than the source IP address"*, and *"care must be taken to
  prevent it from being used to cause a denial of service by locking out other users' accounts."* The first
  keeps this repository's existing key (`login:sha256(email)`); the second is what the old shape got wrong
  from the other side, since a window that re-arms on every refusal cannot be bounded.
- **Keycloak's brute-force detection** (`server_admin` guide,
  <https://www.keycloak.org/docs/latest/server_admin/index.html>, read this round) — it tracks failures
  *per user* and *per IP/agent address* as two dimensions, and its `Temporary Lockout` decays through
  `wait increment` up to `max failure wait`. Borrowed: a refusal must state a **bounded** wait, which is
  what `Retry-After` is now. Not borrowed: permanent lockout, and the address dimension — that one needs a
  client address this stack does not have (the residual is written out in `RELEASE_CHECKLIST.md` row 24).
  The retrieved page text does not list the numeric defaults, so none are claimed here.
- A cheat sheet dedicated to rate limiting was searched for and is **not claimed**: the URL the search
  returned (`.../cheatsheets/Rate_Limiting_Cheat_Sheet.html`) answers 404, so the fixed-window-vs-sliding
  choice is attributed to the measurement in row 24, not to a document.

| candidate | fit | licence | activity | risk | quality | adaptation cost |
|---|---|---|---|---|---|---|
| In-repo: reuse `_mfa_incr` (the fixed-window Lua the second factor and the step-up wall already use) + failures-only counting + `Retry-After` (chosen) | the whole requirement is three behaviours: a refusal must not extend the window it reports, only a failed credential may spend it, and the remaining wait has to be named — all on this key and this store | own code | n/a | nothing new becomes reachable: the gate only reads, the writer is the same atomic script two other gates already run on, and knowing the password buys no budget | one shape now governs all three credential gates, and `tests/unit/test_login_limiter.py` pins each behaviour with its own mutation arm | lowest: no new dependency, no new container, no deployment change |
| `limits` 5.8.0, with `slowapi` 0.1.10 as the FastAPI surface | supplies exactly the vocabulary this decision needed — `FixedWindowRateLimiter`, `MovingWindowRateLimiter`, `SlidingWindowCounterRateLimiter` per its API pages (<https://limits.readthedocs.io/en/stable/api.html>, read this round) — plus Redis storage | MIT for both, read from the PyPI JSON API fields (`license_expression: MIT`, classifier `License :: OSI Approved :: MIT License`), not from a README | `limits` release uploaded 2026-02-05, `slowapi` 2026-06-13; `requires_python` `>=3.10` and `<4.0,>=3.7` respectively | it limits on the remote address, and this stack has no trustworthy one: uvicorn is started without `--proxy-headers` or `FORWARDED_ALLOW_IPS` (`services/api/Dockerfile:13`, and `FORWARDED` has zero hits repo-wide), so every request arriving through the gateway presents the gateway container — one bucket for the whole site | maintained and well-formed; it is the reason "fixed window" is now the word used in the code | a runtime dependency added to the API image, a second limiter store, decorators on a route that already has an atomic script, and it still would not close the account-DoS the OWASP sentence warns about |
| `django-ratelimit` 4.1.0 | cache-based limiting for Django — the framework is the disqualifier | Apache-2.0 (its own `license` field) | last release uploaded 2023-07-24 | n/a | n/a | not applicable: this API is FastAPI |

**What was borrowed:** Keycloak's `max failure wait` thought (a bounded, self-reported wait), and `limits`'
strategy vocabulary (the code and the docs now say *fixed window* instead of describing what the Lua does).
**Why nothing was adopted:** the repository already held both hard parts — an atomic INCR with conditional
EXPIRE, and the failures-only / success-clears semantics `_step_up_gate` documents — while the missing part
(a per-address dimension) is a deployment property, not a package.

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
`2 roster walks with offer/accept/re-role/revoke/remove exercised`.

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

**Readings from the authoritative run** (`acceptance-20260926T122943Z`, commit `414752d`, fresh database created by
`down -v` before start, 09:58:44Z → 10:16:39Z): 19 steps PASS + capacity skipped across 20 rows; 210 unit tests;
`authority matrix agrees with the code: 89 routes, 48 writes` with 5 step-up routes; acceptance 11 passed twice;
contract 5; chaos; lease 8 jobs / 2 workers / 160 credits once; fidelity 2 workspaces and 2 assets byte-identical
with ledgers unchanged; erasure 53/53; mfa 56/56; member 47/47; reconciliation 15/15; 100-run provider regression
100/100 at p50 6.35s / p95 7.9s settling 1000 credits; generic-REST 25/25 at p50 5.17s / p95 6.31s settling 250;
browser 96 view records over 70 axe scans with critical 0 / serious 0 and 52 moderate, zero uncaught errors, 12
console entries (8 expected 401 network lines, 2 expected 409 from the roster refusal, 2 expected 403 from the
wrong-password attempt), 4 privacy states / 8 roster states / 5 second-factor states, 2 exports of 2614 and 2613
bytes and 2 probe accounts erased. The run that found the `[hidden]` defect is kept as a discovery record
(`acceptance-20260926T093142Z`, step 19 FAIL, with `browser-a11y-20260926T094118Z/report.json`).


## 2026-09-26 enrolment hands over a QR, and two reds turn out to be about who is answering the port

Authoritative run `acceptance-20260926T122943Z` (commit `414752d`, fresh database, 2026-09-26T12:29:43Z → 2026-09-26T12:46:47Z,
20 rows with 19 PASS and capacity skipped by switch, host load "22.05 22.50 23.10" on a
4-cpu Docker VM). Unit ladder 229. `scripts/mfa_drill.py` 56/56,
`scripts/erasure_drill.py` 53/53, `scripts/member_drill.py` 47/47, reconciliation
15/15. The 100-run provider batch came back at 100/100 completed, error rate 0.0%, p50 4.14s, p95 5.5s, settled 1000 credits across 100 finished jobs
where the previous round measured p50 6.35s / p95 7.9s on the same fixture; the generic-REST batch is
25/25 completed, error rate 0.0%, p50 4.12s, p95 4.29s, settled 250 credits across 25 finished jobs. Browser: browser-a11y-20260926T124244Z, 96 views /
70 axe scans, critical 0 / serious 0 / moderate 52,
918 elements marked `hidden` with 0 of them still rendering,
2 exports downloaded and parsed, 2 probe accounts erased (a11y-erasure-desktop-0926T124431Z@example.local, a11y-erasure-mobile-0926T124631Z@example.local).

**Enrolment now renders the provisioning URI as a QR the drill scans back.** Until today the panel
printed an `otpauth://` string and asked a human to type a base32 seed into an authenticator.
Candidates compared (all three links opened this session):

| candidate | licence | signal |
|---|---|---|
| `segno 1.6.6` (chosen) | BSD-3-Clause, read from the endorsement clause in the `LICENSE` shipped inside the installed dist-info; PyPI classifier "OSI Approved :: BSD License". Pure Python, zero dependencies | renders PNG data URIs directly (`png_data_uri`), so nothing is written to disk and nothing is injected into the DOM |
| `python-qrcode 8.2` | not installed here and its `LICENSE` was not opened, so no licence claim is made for it | the usual answer, but it pulls a second package (`pypng`) to do what segno does inline |
| a vendored JS encoder in the browser | n/a | would move secret material into client code and re-open the CSP question for inline script |
| a hosted QR image service | n/a | rejected outright: the provisioning URI *is* the second factor's seed, and sending it to a third party is the threat, not the feature |

The decode side is `zxing-cpp 3.1.1` (Apache-2.0 per its installed metadata), which is a drill
requirement only -- `scripts/requirements-drill.txt`, installed in no image, so the release artefact
still ships segno and nothing else.

The shipped CSP (`nginx.conf`, `img-src 'self' data:`) allows a data URI and forbids inline script, so
the server hands back `qr_png_data_uri` and `app.js` assigns it to `img.src` -- no `innerHTML`.
Proof comes from the receiving end: `scripts/e2e_client.py` grew a stdlib PNG reader (chunk walk with
CRC verification, all five filters, 1-bit unpack written from the PNG chunk/filter model of ISO/IEC 15948) and the drill
decodes the response with `zxing-cpp 3.1.1`, requiring the decoded text to equal the provisioning URI
byte for byte. An error level M symbol at scale 4 is asserted against `symbol_size()` so the raster and
the model cannot drift apart. 13 unit guards in `tests/unit/test_mfa_qr.py`, and the browser walk
rasterises the panel and asserts the alt text and the data URI shape.

**Red one, kept: `acceptance-20260926T112458Z` (step 10).** `test_provider_emulator_contract` blew its
20s read timeout on `POST /v1/jobs`. `create_job` is an `async def`, and its idempotency-replay branch
called `resolve()`, which synthesised every missing clip inline -- measured 9.98s for an eight-candidate
replay, with an unrelated `GET /health` queued behind it at 9.95s and an 86-second hole in the emulator's
access log. Clip synthesis costs 0.75-1.0s per candidate in that container, so one client's poll stopped
the whole process; host load (29-36 on a 4-cpu VM) is what turned 10s into a timeout, which is why the
same tree had passed this step twice. Generation moved off the request path, publishing stays ordered
(`results_json` is written only after every clip exists, because a caller that sees `completed` fetches
`audio_url` immediately), a generator that raises publishes failed candidates instead of hanging, and a
clip lands by rename so a killed process cannot leave a torn file that `if not path.exists()` reads as
done. Bytes are unchanged: three clips written by the pre-fix code hash the same as the same call now.
The guard injects a 0.5s-per-clip fake `write_wav` so it is deterministic instead of timing-dependent,
and it was fired against the pre-fix file (`an idempotent replay waited 4.06s`).

**Red two, kept: `acceptance-20260926T115637Z` (step 19) -- and it was never this release's failure.**
Steps 1-18 were green; the browser gate could not find `#loginForm`. At 12:04Z a *different project on
this machine* started `vite preview`, which bound port 4173 on IPv6, and `localhost` prefers `::1`: from
12:10Z the gate was driving that other site. `127.0.0.1:4173` still answered with this repo's page (it
contains `id="loginForm"`), `[::1]:4173` answered with a `/vite.svg` app. A red there is honest, but a
green under the same collision would have issued this release's accessibility certificate for somebody
else's page. Two changes: every host-side default under `scripts/` now uses a literal address (14 sites,
pinned by `tests/unit/test_host_side_endpoints.py` with a denominator and a planted-shape control), and
`browser_a11y.py` refuses to walk until each origin's first paint is byte-identical to the `index.html`
the image `COPY`s -- fetched through the browser's own request context, because the first version used
`urllib`, took the IPv4 path, and *passed* while Chromium was still reaching the intruder. The check
also catches an image built from a different tree. Both arms were fired against the live collision: on
`localhost` it exits naming the foreign `<title>` and both hashes; on the default it verifies studio and
control plane byte for byte and completes the walk.

## 2026-09-26 joining a workspace waits for the person it was offered to

Authoritative run `acceptance-20260926T232902Z` (commit `fc70d13`, fresh database, 2026-09-26T23:29:02Z → 2026-09-26T23:43:18Z, ['static-verify | PASS', 'compose-up | PASS', 'acceptance | PASS', 'contract-test | PASS', 'chaos-worker-recovery | PASS', 'lease-contention | PASS', 'restore-fidelity-snapshot | PASS', 'backup-restore | PASS', 'restore-fidelity-compare | PASS', 'acceptance-rerun | PASS', 'erasure-drill | PASS', 'mfa-drill | PASS', 'member-drill | PASS', 'commercial-flow | PASS', 'reservation-race | PASS', 'market-reconciliation | PASS', 'provider-regression-100 | PASS', 'generic-rest-roundtrip | PASS', 'browser-a11y | PASS', 'capacity-gate-500 | SKIPPED'] rows with 19 PASS and capacity skipped by switch, host load "5.81 4.99 5.67" on a 4-cpu Docker VM), browser record `release-evidence/browser-a11y-20260926T233856Z/report.json`. Unit ladder 289, `scripts/member_drill.py` 98/98, `scripts/erasure_drill.py` 53/53, authority matrix 96 routes / 51 writes; the browser gate scanned 116 views with 90 axe runs, ['workspace-team', 'workspace-team-accept-dialog', 'workspace-team-accepted', 'workspace-team-inbox', 'workspace-team-offered', 'workspace-team-pending', 'workspace-team-remove-blank', 'workspace-team-remove-dialog', 'workspace-team-removed', 'workspace-team-revoke-dialog', 'workspace-team-role', 'workspace-team-role-dialog'] team-panel states, [] uncaught errors and [] CSP-blocked inline styles, violations by impact {'moderate': 64}.

**Why this round exists.** Release-checklist item under 待属主定值 7 said the product could add a workspace member by typing an e-mail address, and reading the code showed that one call did two jobs at once: it resolved the address against `users` and answered an unknown one with its own sentence (`db/migrations/017_workspace_membership.sql:58-61`), which makes every owner and admin a probe for "does this address have an account here"; and on a hit it wrote `workspace_members` at :71 with no step in which the named person agreed. The repository has no mail, invite or confirmation channel (grep smtp / sendgrid / mailgun / ses / boto3 / nodemailer: zero consumers) and no registration endpoint, so there was nothing to lean on.

**What `db/migrations/019_workspace_invitations.sql` puts in its place.** An offer is its own row -- `workspace_invitations`, with `token_hash char(64) UNIQUE`, `expires_at`, one-way settle columns, a partial unique index of one live offer per (workspace, address), and a `BEFORE UPDATE` guard that makes address, role, token and lifetime immutable and a settled row impossible to re-open. Six `SECURITY DEFINER` functions carry every access to it, mirroring 017/018; the table gets no DML grant, only the `SELECT` that the subject export's per-table read needs. The membership write lives inside `accept_workspace_invitation`, next to the three liveness predicates, and its authority is the comparing of the signed-in account's own address with the one on the offer: the token proves the link arrived, not that the person agreed. `create_workspace_invitation` never queries `users` by address at all, so there is no branch to phrase differently -- which is the half that closes the probe, and the reason the resident test compares two responses key by key rather than looking for a friendly error string. The old door is not wrapped, it is closed: 019 ends with `REVOKE EXECUTE ON FUNCTION add_workspace_member(uuid, uuid, text, text) FROM music_app`, so the application role cannot reach the probe even from a hand-written statement.

**Three things the feature dragged out, each measured.** A trigger on `users` settles live offers for the address an account gives up, because 013:94 replaces `users.email` with a pseudonym at erasure and a pending offer would otherwise keep a real address alive for a subject who no longer exists. The subject export declares `workspace_invitations.created_by/.used_by/.revoked_by`, taking `PERSONAL_TABLES` from 19 triples to 22, because the erasure drill's coverage census is derived from `information_schema` and turns the drill red when a migration adds a user key (53/53 unchanged after). And the interface had a real defect: an account with zero memberships could not sign in at all -- `POST /api/auth/login` 200, then `GET /api/bootstrap` 400, `init()` throwing the visitor back to the form with no message -- because four boot loaders unconditionally asked for a workspace. That is now an empty state the invitee can read and act on, rendered as empty rather than as loading, and the browser walk is what found it: it is also the walk that taught the two lessons about locators written down in checklist item 21.

**Proving the tests have teeth.** Two arms, both against the live server rather than a mock: the drill runs green at 98/98 twice, then `create_workspace_invitation` is redefined with the address probe put back and `accept_workspace_invitation` with the e-mail comparison deleted, and the same drill answers 9 FAIL, naming the token-is-not-authority check (`... cannot spend the token in its hands -- 200`) and the enumeration check (`... whether an address has an account -- absent={}`). The pristine bodies are then re-applied from the migration file and the drill is green again, so the red is attributable to the withdrawn guarantee and not to the fixture.

## 2026-09-27 two rounds' worth of claims turn out to be about the machine, not the code

Authoritative run `acceptance-20260927T040234Z` (commit `cb8b901`, fresh database requested as `FRESH=1` and recorded as `fresh_database=1` in the same header, 2026-09-27T04:02:34Z → 2026-09-27T04:18:18Z, 21 rows: ['static-verify | PASS', 'compose-up | PASS', 'acceptance | PASS', 'contract-test | PASS', 'chaos-worker-recovery | PASS', 'lease-contention | PASS', 'restore-fidelity-snapshot | PASS', 'backup-restore | PASS', 'restore-fidelity-compare | PASS', 'acceptance-rerun | PASS', 'erasure-drill | PASS', 'mfa-drill | PASS', 'member-drill | PASS', 'media-scan-drill | PASS', 'commercial-flow | PASS', 'reservation-race | PASS', 'market-reconciliation | PASS', 'provider-regression-100 | PASS', 'generic-rest-roundtrip | PASS', 'browser-a11y | PASS', 'capacity-gate-500 | SKIPPED'] with 20 PASS and `capacity-gate-500` skipped by switch, host load "6.02 15.30 13.43" on a 4-cpu Docker VM), browser record `release-evidence/browser-a11y-20260927T041301Z/report.json`. Step 1: unit ladder 313, authority matrix 96 routes / 51 writes. `scripts/media_scan_drill.py` 14/14, `scripts/member_drill.py` 98/98, `scripts/mfa_drill.py` 56/56, `scripts/erasure_drill.py` 53/53, `scripts/reconcile_market.py` 15/15, provider regression 100/100 at error rate 0.0% and generic-REST 25/25, lease contention 8 jobs across 2 workers with 160.0 credits settled once, restore fidelity 2 workspaces and 2 assets byte-identical; the browser gate scanned 116 views with 90 axe runs, 12 team-panel states, 5 second-factor states, 4 privacy states, 2 roster walks, 58 mobile-fit measurements, zero uncaught errors, zero CSP-blocked inline styles and violations by impact {'moderate': 63}.

**Re-certified after the later fixes in this same round.** `acceptance-20260927T070318Z` (commit `cc63bee`, `FRESH=1` fresh database, 2026-09-27T07:03:18Z → 2026-09-27T07:29:50Z, 21 rows: 20 PASS and `capacity-gate-500` skipped by switch, host load "25.52 13.84 10.07" on a 4-cpu Docker VM, `disk_free_kb=16905088` in the same header), browser record `release-evidence/browser-a11y-20260927T072517Z/report.json`. The certification above it (`cb8b901`'s run, 04:02:34Z → 04:18:18Z) is now one of the prior green runs; this one additionally covers the CI payload census, the un-shadowable capacity profile and the disk precheck, and its step 1 ran 337 unit tests.

**Why this round exists.** Two steps of the chain were measuring something other than what they
claimed. Step 6 read as a lease-contention failure while it was really a build-alignment failure:
`acceptance-20260927T030055Z` shows `worker-b` (profile `contention`) running an image hours old,
because the chain derived its build list from `docker compose config --services`, and that listing
does not report profile-gated services -- so the "two workers" in the contention reading were one
new worker and one stale one, and the stale one wrote the literal `clean` into `media_assets` the way
code before 020 did. Step 5 read as a recovery failure while it was really a second-claimant
failure: `acceptance-20260927T035001Z` printed "expired lease was not reclaimed by the restarted
worker" at the moment `generation_jobs.lease_owner` was `worker-0a906393`, the startup identity of a
`worker-b` that had been started by hand 18 minutes earlier and was still heartbeating, while the
worker that step kills identifies itself as `worker-e02a9f18`. Neither red said what it meant, and
each was attributed to the code by a reader who had to dig it out of the database afterwards.

**What changed.** `step_stack_up` now derives its build list from `docker compose --profile '*'
config --format json` filtered to services that define `build` (measured on this host: `acceptance`,
`api`, `gateway`, `migrate`, `payment-emulator`, `provider-emulator`, `web`, `worker`, `worker-b`;
`clamav` has no build section and is left to whoever asks for the `scan` profile).
`scripts/lease-contention.sh` refuses to submit a job unless the host's `services/worker/worker.py`
hashes equal `/app/worker.py` inside both claimants. `scripts/chaos-worker-recovery.sh` removes
`worker-b` on entry, the way `lease-contention.sh` always has, and refuses to proceed unless compose
reports exactly one running service whose name starts with `worker`; its drill now prints the status
and attempt count it last read instead of asserting a cause it cannot see over HTTP (no
`lease_owner` is exposed by any endpoint). The chain's pre-run environment check gained `sha256sum`
and `xargs` because a PATH without `/sbin` turns step 1 into `tracked=0 listed=229 stale=229`, which
reads like a broken manifest rather than a missing host binary. `FRESH=1` makes the pipeline itself
clear the volumes and records `fresh_database=` in the header, so "run on a fresh database" stops
being a promise about the operator. `docs/E2E_ACCEPTANCE_RUNBOOK.md` §4 documented a 10-step chain
including a `unit-test-docker` step that exists nowhere in the script; it now carries all 22.

**Where the guard itself was the defect.** The ratchet meant to protect step 2 pinned the literal string `$(docker compose config --services)` -- it had frozen the
buggy spelling as the requirement, which is why it stayed green through the whole defect. It now
judges the shape (enumeration derived from `compose config`, profile-wide, fail-fast) and carries a
control that feeds it the profile-blind loop and asserts that only the new clause reddens. Verified
by mutating the real file: `scripts/acceptance-all.sh` at sha `4d5bfe9c3371` passes, the same file
with the profile-blind loop restored at sha `2de64be79e24` fails 2 tests, and `4d5bfe9c3371` again
passes 10. The preflight's own two sides were measured against the live stale container: tree
`6b13cb247c227cad`, `worker` `6b13cb247c227cad`, `worker-b` `a7cb1bb8d5ae10f7` before the rebuild and
`6b13cb247c227cad` after it, and the certified run logs both `contention preflight:` lines agreeing.
The single-claimant count is `2` with `worker-b` up and `1` after `rm -fs`, on both the plain
`docker compose ps` and the `--profile '*'` form -- which is worth writing down, because `ps`
reports a *running* profile-gated container without the flag, so this predicate is not the same trap
as `config --services` was.

**The same question asked of CI.** The chain dispatches seventeen scripts; the workflow dispatched
fourteen of them. `scripts/media_scan_drill.py` -- the drill that proves a media asset cannot call
itself clean without a scanner behind it, i.e. the whole point of migration 020 -- had a chain step and
no CI job, and `scripts/restore_fidelity.py` (the byte-and-ledger fingerprint around backup/restore) was
measured only on this host. Both were invisible to every existing guard because nothing compared the
two dispatch lists. `tests/unit/test_ci_covers_chain_payloads.py` now does, with its own red side:
deleting the media-scan step from the real `.github/workflows/ci.yml` (sha `a559d952103e` ->
`0b72189cd969`) fails `test_every_chain_payload_is_dispatched_by_ci_or_exempt`, and restoring it makes
the file's six tests pass again. The one exemption, `test.sh`, states the reading that earns it (CI runs
`docker compose --profile test run --rm acceptance` inline) and a test re-checks that that line is still
there, so the exemption cannot outlive the fact.

**Unverified, and why:** the CI jobs themselves were not observed running. This repository is private,
`gh` has no session here and the GitHub connector exposes no workflow-run read, so adding a step is a
coverage claim, not a pass claim; the equivalent steps are green on this host (media-scan drill 14/14 at
chain step 14, restore-fidelity snapshot and compare at steps 7 and 9 of
`release-evidence/acceptance-20260927T040234Z/`), and that is the strongest reading available locally.

**A third thing two of this round's reds had in common: a reading blamed on the machine that the
configuration had actually produced.** `docs/RELEASE_CHECKLIST.md` carries `capacity-gate-500` as
"judged red on this host, host capacity", and the file's own capacity profile asked for four Uvicorn
workers -- but it asked as `${API_WORKERS:-4}`, and Compose resolves that from the host environment and
`.env`, which every host here has (`capacity-gate-500.sh` copies `.env.example` into place, and `.env.example`
ships `API_WORKERS=1`). `docker compose exec api sh -c 'echo $API_WORKERS'` answered 1 while the profile
said 4, so every capacity number ever recorded was taken on one worker. Pinning the literals makes the
container answer 4, with four `Started server process` lines behind it, and `tests/unit/test_environment_contract.py` now forbids a profile value that the host can shadow.
Re-measured with only the worker count changed, each arm recreated and repeated on this 4 vCPU VM
(GET /api/projects via the gateway): median latency does move -- 128 users x 5 gives p50
1089.9/1091.5ms on one worker against 577.3/723.2ms on four, 500 x 2 gives 7271.8/6375.4ms against
3508.8/5000.2/5445.8ms -- but p95 and throughput do not separate reproducibly (the four-worker arm read
p95 1152.0ms on one pass and 4517.9ms on the next at 128 users), so the claim "128 concurrent passes the
800ms budget on four workers", which a single first pass appeared to support, is **withdrawn**. The 500-user
conclusion is still not obtained on this host; what changed is that the red is now attributable to the
host rather than partly to a configuration nobody intended to test. During one 500-user run
`docker stats --no-stream` showed `loadtest` 68.79%, `api` 71.07% and `postgres` 89.28% CPU -- the load
generator, the service and the database share the four cores, which is why the table in
`docs/COST_OPTIMIZED_500_CONCURRENCY.md` reports repeats instead of single numbers.

## 2026-09-28 three parked decisions closed, and the accessibility gate loses its tolerance

Authoritative run `acceptance-20260927T173103Z` (commit `2f25fe5`, fresh database requested as `FRESH=1` and recorded as `fresh_database=1` in the same header, 2026-09-27T17:31:03Z → 2026-09-27T17:53:30Z, 21 rows: 20 PASS and `capacity-gate-500` skipped by switch, host load "9.18 16.02 23.16" on a 4-cpu Docker VM, `disk_free_kb=7808440` beside it), browser record `release-evidence/browser-a11y-20260927T174714Z/report.json` — stamped with the same commit, because the code was committed before the chain was started. Step 1: 343 unit tests, `authority matrix agrees with the code: 96 routes, 51 writes`, `tracked=232 listed=232 missing=0 stale=0 mismatched=0`. `scripts/mfa_drill.py` 67/67, `scripts/member_drill.py` 98/98, `scripts/erasure_drill.py` 53/53, `scripts/media_scan_drill.py` 14/14, `scripts/reconcile_market.py` 15/15, lease contention `8 jobs, 2 workers, 160.0 credits settled once` with both preflight lines agreeing (`worker` and `worker-b` each `== the tree (6b13cb247c227cad)`), restore fidelity `2 workspaces, 2 assets byte-identical, ledgers unchanged`, provider regression `100/100 completed, error rate 0.0%` at p50 9.77s / p95 12.99s settling 1000 credits, generic-REST round trip `25/25` at p50 6.4s / p95 10.17s settling 250, and the browser gate `118` view records over `92` axe scans with `violations_by_impact` **empty** under `blocking_impacts: [critical, serious, moderate]`, 59 mobile-fit measurements, 1291 elements marked `hidden` with 0 still rendering, 2 exports (2731 and 2730 bytes) and 2 probe accounts erased, 0 uncaught errors, 0 CSP-blocked inline styles, 4 privacy states / 5 second-factor states / 12 team-panel states / 4 platform-console roster states, and `boot_window_clicks: 2`.

**Why this round exists.** Three rows of `docs/RELEASE_CHECKLIST.md` §待属主定值 had been re-copied for rounds as decisions somebody else owed: the login limiter's window semantics ("a one-line remedy… left to the owner"), a first-paint click that does nothing, and 52 — later re-measured as 64 — axe findings filed as "follow-up". All three were code, in this repository, verifiable on the running stack. The tolerance that let the third sit was itself the defect: the gate blocked `critical` and `serious` only, so a screen with no landmark and twenty views with a skipped heading level were reported as a number in a document while every gate read green.

**The login window.** `services/api/app/main.py:159-181`: `_login_gate` reads the counter and the key's TTL and raises 429 with `Retry-After`, writing nothing; `_login_failed` runs only on a bad or absent credential, through `_mfa_incr` (`:239`) — the atomic INCR-with-conditional-EXPIRE script the second factor and the step-up wall already used, so all three credential gates on this surface now share one definition of "a minute". The deployed limit is unchanged (`LOGIN_RATE_LIMIT_PER_MINUTE=10`, read back from the container rather than assumed), so guessing is still bounded at ten a minute per account; what changed is that ten *failures* are ten failures, and that a refusal no longer extends the window it reports. `scripts/mfa_drill.py` grew eleven checks that read the store instead of inferring from status codes (56 → 67): twelve consecutive successful logins leave the key absent; the first 429 arrives at attempt 11 and the sequence is monotone; a correct password is refused while the window is full (the wall sits before the credential check); `Retry-After` agrees with the TTL; further refusals leave the stored count at exactly 10; and the window must keep counting down **while** it is being knocked on — in this run, 45 refused requests over 2.0s with the TTL falling from 59. That last check was rewritten after its first version passed on the pre-fix code: it slept *after* the refusals, so even a re-arming limiter had decayed by the time it was read.

**Teeth, on both sides.** Five mutation arms against the real `main.py`, each reddening exactly one clause of `tests/unit/test_login_limiter.py` (gate moved after the DB read; gate made to increment; the shared script restored to unconditional `EXPIRE`; `Retry-After` removed; the failure branch made unconditional), with the file restored to sha `824823368d85` afterwards. On the live stack `main.py` was reverted to the pre-fix limiter and the api image rebuilt: the same drill answered **7 FAIL**, naming `statuses: [200 ×10, 429, 429]`, `key login:bc17c… reads 12 after 12 logins`, `first refusal at attempt 1`, `Retry-After '' against TTL 59`, `counter read '28' then '51' against a limit of 10`, and `TTL 59 then 59 after 2.1s of refusals`; the pristine file was restored, the image rebuilt, and the drill reads 67/67 — which is now a chain step, so the certification above includes it.

**Accessibility: 64 → 0, then the bar moved.** Attribution first. Splitting the previous certified run's `report.json` by rule × view × target showed `region` (66 nodes) and `landmark-one-main` (12) coming from five selectors, all of them on the sign-in screen, and `heading-order` (40) as exactly two nodes in each of twenty views. Fixes: `#login` becomes a `<main>` **in place** (`services/web/index.html:13`, `services/admin/index.html:5`) rather than gaining a wrapper, because `.login-shell` is a two-column grid (`styles.css:200-204`) and `.login-card` has no height of its own (`:269-276`) — a wrapper would have stopped the card filling its column and lost its vertical centring; and panel titles `h3`→`h2` with subsections `h4`→`h3` (15 + 6 headings in `services/web/index.html`, four card templates in `app.js`), with the sizes stated explicitly in CSS because neither stylesheet used to state a heading size at all — the level was supplying it through the UA sheet, so changing the level without pinning the size would have moved the interface. `BLOCKING_IMPACTS` (`scripts/browser_a11y.py:43`) is now `(critical, serious, moderate)`, and the gate's own `--self-test` carries both polarities of that decision — moderate must fire, minor-only must not — printing `the gate rejects it, rejects moderate, and still tolerates minor-only`. `tests/unit/test_browser_a11y_gate.py` previously asserted the opposite policy in prose ("moderate is tolerated per the documented criterion"); it now asserts the new one and requires the finding to name its impact, so a red says which rule moved.

**What the widened gate found on its first run.** Two `heading-order` nodes reported as `h5`, in `account-privacy-exported` and `account-privacy-erasure-refused`: the export summary builds its "deliberately excluded fields" heading with `document.createElement('h4')` (`services/web/app.js:2518`) under a panel title that is now `h2` with an `h3` above it. It had never been seen for two reasons — the impact was moderate, and the element exists in no text face a grep could match, while `styles.css:105` was still styling the `h5` it used to create. Both faces now say `h4`. Kept as a discovery record: `release-evidence/browser-a11y-20260927T164130Z/report.json`, whose `violations_by_impact` is `{moderate: 2}`.

**The click that used to do nothing.** `#app` became visible two awaited fetches before `bindNavigation()`, and the gate's own `press_until()` clicked until it worked — a workaround standing where the evidence should be. Handlers are now bound before the app is shown, and boot ends with `navigate(state.view)` so a click made during loading is the view that survives (`services/web/app.js:447-457`; `services/admin/admin.js:260` for the same invariant, where no window existed yet but one inserted `await` would create one). `walk_boot_click()` (`scripts/browser_a11y.py:420-465`) holds `/api/bootstrap` for 3s, reads both premises back before judging (the request really reached the handler; `#creditPill` still read `— Credits` at click time — a product-visible state, not a test clock), clicks **once**, then requires the held fetch to return and be painted. Both arms ran against the same container: control at `88cc23e403739487` → no finding; the pre-fix `app.js` deployed into the container at `c625ce68cf6abd2e`, with the hash read back from inside the container → `a single nav click at '— Credits' (boot still loading) did not switch the view`. The first attempt at that arm produced a false "no finding" because the copy ran *before* the file was generated; recorded here because it is the same failure the check exists to catch.

**A skip link that bypassed to a hidden element.** Both apps' `.skip-link` pointed at `#main`, which lives inside the hidden `#app`, so on the sign-in screen Enter moved focus nowhere — the bypass-blocks requirement failing on the one screen where the app *is* only a form. `setAuthScreen` now sets the href per state (`services/web/app.js:320-325`, `services/admin/admin.js:263`/`:274`) and the gate refuses to walk an origin whose skip target is not rendered (`scripts/browser_a11y.py:355-365`).

**Layout, measured rather than assumed.** The heading levels changed, so sizes were pinned; the before/after screenshots of the same states (`browser-a11y-20260927T072517Z` vs `browser-a11y-20260927T174714Z`) show panel titles at the same size and position, and the gate's own clip, overflow and contrast checks are green — which is the part a human eye would otherwise be asked to vouch for.

**Test-harness consequence.** The 75-second backoffs existed only because the window slid; they are 61 seconds now in `scripts/e2e_client.py:84`, `scripts/member_drill.py:94` and `scripts/browser_a11y.py:385`, and the comments that justified 75 were rewritten to describe the current mechanism rather than the old one.

**Residuals, stated rather than hidden.** One account can still be held down by one determined knocker, a minute at a time: bounding that needs an address dimension, and the API has no address it may trust — `services/api/Dockerfile:13` starts uvicorn without `--proxy-headers` or `FORWARDED_ALLOW_IPS` (zero hits repo-wide), `services/gateway/nginx.conf:26-28` *appends* `X-Forwarded-For` so a client-supplied value survives, and the only two reads of `request.client` (`main.py:207`, `:223`) are audit-only. Keying on what the API sees today would put every user in one bucket. The fix is two deployment changes plus a trust list, not a line of Lua, and it is registered as such. `minor` stays out of the blocking set because no rule in axe 4.13.0 reports it on these pages — a criterion nobody can trip is not a criterion, and the self-test's minor-only arm is what would notice if the set were widened past green. And the CI jobs remain dispatched-and-unreadable from this environment, so every number above is a local run.

## 2026-09-28 a public inbox for rights notices, built so that it answers no question about what the platform holds

Authoritative run `acceptance-20260927T194152Z` (commit `037b818`, fresh database requested as `FRESH=1` and recorded as `fresh_database=1` in the same header, 2026-09-27T19:41:52Z → 2026-09-27T19:57:19Z, 22 rows: 21 PASS and `capacity-gate-500` skipped because `CAPACITY=1` was not set, started under host load "5.02 4.64 5.21" on a 4-cpu Docker VM, `disk_free_kb=8442608` recorded beside it).

**Why this round exists.** `docs/RELEASE_CHECKLIST.md` §G10 carried one box -- 内容审核、病毒扫描、投诉和 Legal Hold -- whose complaint half had been blocked for rounds on a premise nobody had tested: that an intake needs an account, and this repository has no public registration endpoint. It does not. What an intake needs is a table, a receipt and a refusal to be a query interface. `db/migrations/021_public_rights_report.sql` is that: `rights_reports`, five `SECURITY DEFINER` functions, and four endpoints, two of which are reachable with no credential at all.

**The three properties, each measured on both polarities.** A filing against a real asset id, against a random uuid and against a malformed string returns the same keys, the same status word and the same body once the three fields that are unique by construction are blanked -- so the door cannot be used to ask whether the platform holds something; this is the same reasoning that made 019 stop resolving addresses. The reporter's e-mail lives only in `rights_reports`, and the case a tenant can list carries the reference, the ground, the location and the work identification but never the address -- the isolation is not a filter but an absence, because `services/api/app/routers/assets.py:232` reads that case with `SELECT *`. And the notice is evidence rather than a ticket: four arms attempt UPDATE and DELETE as `music_admin`, which `pg_roles` reports as `rolsuper=t, rolbypassrls=t`, and all four are refused by the trigger, because a GRANT-only claim would be worth nothing against this role.

**Two defects this slice produced, both now closed with residents that fire.** Moving `_db_refusal` out of `app/main.py` (the router needed it and importing back into `main` is a cycle) carried the function but not its `HTTPException` import; because the module defers annotations, the file imported, 100 routes registered, the unit ladder and the image build both went green, and the first caller actually refused by the database got a 500 whose only trace was a `NameError` in the container log. `tests/unit/test_db_refusal.py` now *calls* the mapper with real `psycopg2.Error` objects -- an AST reader would have seen a perfectly well-formed body -- and five arms were verified against the source: drop the import (7 red), narrow the link door's handler back to one subclass (1 red), strip the intake door's handler (3 red), let a CHECK violation quote the database's sentence including the table name (1 red), and the restore re-checks the file's sha1. The second defect is a rule written twice: the request model counts characters where the table counts characters trimmed, so a three-space work identification passes validation, fails `report_work_identification_present` and arrived as a 500. SQLSTATE 23514 now answers 422 carrying the constraint's name and nothing else, and the drill asserts the name per field for all four length-checked columns, with "and it wrote nothing" beside each.

**Where the vocabulary actually lives.** This round's own parity guard found that the subject-type and ground lists are written *three* times, not twice: the Python constants, the two `CHECK (col IN (...))` constraints, and a third copy inside `file_rights_report` as `= ANY (ARRAY[...])` predicates. `tests/unit/test_report_surface.py` (28 cases: 14 guards, 14 must-fire controls holding 33 in-memory injections) compares all three pairwise, checks that the public doors carry no `Depends` and the staff doors carry `require_platform_admin`, and pins the deliberate *strict subset* in `report_subject_workspace` -- `user` and `other` resolve to no workspace, which is the anti-oracle design, so an equality assertion there would be a bug.

**Wiring.** `report-drill` is chain step 15 (50/50 live checks, on the freshly built image rather than a copied one) and a CI job, with `tests/unit/test_ci_covers_chain_payloads.py` enforcing that the two dispatch lists cannot diverge again. Its teardown is now asserted: three checks that the run left no fixture workspace, report or case behind -- the workspace used to leak, and one accumulation of eight of them is what made that visible. Reading the count out of psql turned into a lesson of its own: psql prints a command tag for a data-modifying statement even in tuples-only mode, so `SET ...; SELECT count(*)` puts the word `SET` above the number and `INSERT ... RETURNING id` puts `INSERT 0 1` below the row; the helper now takes a value only from a top-level SELECT, bare writes go through a separate call, and a first line that is a tag raises instead of being reported as a count.

**What the Legal Hold sentence in `docs/RIGHTS_POLICY.md` actually means today.** The policy promises five actions on acceptance and a freeze on deletion; the round measured the flag rather than restating the promise. `legal_hold` blocks three doors: market listing (`002:568,574`), offer creation (`002:675,680`), brand award (`004:25,30` and its ambiguous-column correction `009:26,31`), plus `assert_offer_rights` (`commerce.py:48`, live at `market.py:492,510,626`). It does not touch download or export (`main.py:1230` via `domain/rights.py:43`, which reads only the capability status -- the `:1183` this file carried since the intake round had drifted onto `moderation_report_link`, and the drill re-took it), issued licences, payment or split accounting, or the subject's own erasure. "Keep all evidence" is delivered by global append-only triggers, whose list is narrower than the sentence: `rights_evidence` and `licenses` refuse only DELETE (`002:462-463`), and `media_assets` has no DELETE guard at all -- a held asset's media is still deletable. Those gaps, and the question of whose hold counts when the holder is staff and the deleter is the subject, are recorded with their file:line in §待属主定值 item 6 rather than patched with a guessed guard.

**Residuals, stated rather than hidden.** The complaint door still has no address window of its own, but that is now a choice rather than a blocker: the trusted-address machinery landed after this run was taken (next section), so the intake could key a source window on the same digest if the queue ever needs one; today it holds a per-reporter window and a global one. The complaint queue is platform-admin-only by predicate *and* by role, and there is still no workflow around it -- no SLA, no escalation, no notifier -- which is a product decision, not a code gap. And this round's `disk_free_kb` is 8442608 on the same host that ran the previous one at 7808440; the capacity gate remains the row that has never gone green here.

## 2026-09-28 the login door gets a source it may believe, and the audit column stops remembering a container

Closes the open half of `docs/RELEASE_CHECKLIST.md` §已修 item 24 -- the sentence that had survived two rounds as *the reason* no address dimension exists: "the API has no address it may trust". It now has one, and the gate that needed it is on.

**Selection, before the code.** Three candidates were read, not assumed. (1) **uvicorn 0.35.0's own `ProxyHeadersMiddleware`** -- `config.py` shows `proxy_headers: bool = True` (so the middleware is already installed; only the trust list was missing) and `forwarded_allow_ips: list[str] | str | None = None`, which resolves to the middleware's `trusted_hosts: list[str] | str = "127.0.0.1"`, i.e. an unset setting boots as *trust nobody* rather than as a failure. `middleware/proxy_headers.py:126-142` scans `reversed(x_forwarded_for_hosts)` and returns the first host not in the set, and `HostSet.__contains__` (ibid.:111-124) accepts literals, `*`, and anything with a `/` as an `ip_network`. License BSD-3-Clause, active upstream, one dependency already pinned in `services/api/requirements.txt`. (2) **nginx `realip` module / HAProxy `proxy_protocol`** -- rejected on a firsthand reading: `grep -rl proxy_protocol` over the installed uvicorn package returns **no files**, so the binary-protocol form of "the peer the proxy actually saw" is not reachable in this stack without replacing the server, and the `realip` module would add a build dependency to the gateway image for the same answer nginx can already produce with `$remote_addr`. (3) **Parse `X-Forwarded-For` in the application** -- rejected: it re-implements a right-to-left trust walk that the installed server already performs and is the part most likely to get wrong under IPv6 and whitespace. Decision: **reuse (1), borrow nothing else, write no parser**; the only self-authored pieces are the trust-list plumbing and the digest, and the borrow is explicit -- the gate reads `request.client.host`, which is exactly the field (1) rewrites.

**What landed.** `services/gateway/nginx.conf:32,:50` write `X-Forwarded-For $remote_addr` (overwrite, not append) with `X-Real-IP` retained; `docker-compose.yml:288-290` gives the default network an IPAM block, `:251` pins the gateway to `172.28.95.250` -- and that octet is a measured choice, not a taste: the first authoritative run of this design died at chain step 2 with `failed to set up container networking: Address already in use`, because the allocator hands out `.2, .3, ...` in creation order while the gateway is created last, so `web` already held `.10`. `iprange`, the usual fence, is rejected by this compose version (`additional properties 'iprange' not allowed`, measured with `docker compose config`), so the pin sits above the pool's reach; `tests/unit/test_login_source_gate.py` now requires the fourth octet to be >= 128 and `scripts/login_source_teeth.py` carries the arm that restores `.10` and must redden it. After the change `docker network inspect` shows the gateway at `.250` with `web` keeping `.10`, and `up` returns 0 (the red run is kept with its own `SUMMARY.txt` at `release-evidence/acceptance-20260927T221550Z/`, which is why the record's authority is the newest **all-green** directory rather than the newest one -- `tests/unit/test_release_record_consistency.py` now says so and carries the arm that proves the two readings differ), `:120` states the trust list as the **literal** `172.28.95.250` -- deliberately not `${FORWARDED_ALLOW_IPS:-...}`, because a stray host `.env` could then widen it and the file would still read as pinned; `services/api/Dockerfile:16` passes `--forwarded-allow-ips "${FORWARDED_ALLOW_IPS:?...}"` with `:?` so a missing setting fails the boot instead of defaulting to `127.0.0.1`. The gate itself: `main.py:201` `_source_gate` (reads `scard`+`ttl`, refuses 429 with `Retry-After`, consulted at `:230` before `fetch_one`), `main.py:219` `_source_failed` inside the credential-failure branch, `main.py:288` `_login_source_add` = `SADD` + `if redis.call('TTL',KEYS[1]) == -1 then EXPIRE` + `SCARD`. Counting **distinct accounts** rather than attempts is the whole point: five mistypes by one person are that person's business, which the per-account window already holds; one mistype each by twenty accounts is a list being walked, which only a source window can see. Re-arming the expiry on every add would turn it back into a sliding window, which an attacker holds open by knocking.

**The audit column was lying, and unsalted.** `create_browser_session` stored `token_hash(client_ip)` in `auth_sessions.ip_hash`. Two independent defects in one expression: an *unsalted* digest of a value with a 2**32 space (a session dump can be reversed by enumeration, so an old row can be turned back into a subscriber's connection after the fact), and, behind this gateway, a digest of **the gateway container's address** -- so the column recorded the same string for every login in the world while looking per-session. `auth.py:53` `address_hash` now mixes `settings.address_pepper` (`settings.py:47`, listed in `SIGNING_SECRETS` at `:90`, required from the deployer at `docker-compose.yml:121`), and `:145` uses it, so the column and the limiter key `main.py:197` share one derivation. `uvicorn 0.35.0`'s `always_trust` branch is why the guard forbids `*`: with it the middleware returns `x_forwarded_for_hosts[0]` -- the client's own first entry, i.e. the header the whole exercise exists to distrust.

**Teeth, on both sides.** `tests/unit/test_login_source_gate.py` (13 cases) holds the wiring: gate before the read, gate whitelisted to `{scard, ttl, get, int, str, len, _source_key, HTTPException}` so an unseen writer fails closed rather than needing to be named, one spending site and it inside the credential branch, limit from settings not a literal in the body, `Retry-After`+`rq.ttl`, the script's three clauses, the shared digest across two files, the trust list stated exactly once and equal to the pinned address, `:?` not `:-`, and every *active* nginx setter writing `$remote_addr` with the append form forbidden. `scripts/login_source_teeth.py` runs 19 arms against the real files (tracked, re-runnable, and it rewrites the working tree while it runs -- see its own header): 17 must-fire, 2 must-not-fire, all matched their declared red-sets exactly, each arm proven by a sha1 delta before the run and every file restored to its baseline sha1 after (`main.py=6b0bd84f434f auth.py=6713bb6e64f3 docker-compose.yml=d5371dcd3d0a Dockerfile=846c4282b0e9 nginx.conf=4050f00dbbf4`), ladder green at the end. Two of those arms are the reason the file is 13 cases and not 12: an early version judged the gate by `re.search` over `ast.unparse`'s text, which made the guard fire on its own docstring ("no SADD, no DEL, no EXPIRE happens here") -- the N2 arm is what says the AST-call reading is load-bearing; and the N1 arm says the active-line filter is load-bearing on the *shipped* file, which already carries the forbidden variable name in two comments (`nginx.conf:28-31,:46-49`).

**Two harness findings worth keeping.** The battery's first parse looked only for `^FAILED`, so an arm that deleted the gate -- raising `ValueError` on `calls.index("_source_gate")` -- read back as "no detector fired", the exact false green the battery exists to prevent: the parse now takes `FAILED|ERROR`, and the ordering test asserts presence before position, so a removed gate reports "the gate is dead code" instead of crashing. And the accidental deletion arm is kept as a real arm (A2), because silent removal is the failure this family actually has.

**Live readings.** `scripts/mfa_drill.py` went 67 → **79/79** on the running stack: a login through the gateway records the address the gateway itself saw (`:528`), the same forged `X-Forwarded-For` arriving on the published port is ignored because that peer is not on the list (`:536`), one account past the configured 20 from one source is refused with the wait stated (`:561`), and five mistypes by one account are one account (`:585`). Unit ladder **395 passed** across 39 files. No chain run has been re-certified against these changes yet -- the figures above are local, and the next authoritative run restamps the four faces.

**Residuals.** One account can still be held down a minute at a time by one person who knows its address; bounding that needs a self-service path (captcha or recovery channel), which is a product decision and is registered as such, not patched with a guessed puzzle. The source window is on the login door only -- the intake and the second factor keep their own keys. And the trust list now *must* agree with the gateway's static address because a resident test requires it, so moving the gateway means editing `docker-compose.yml:120` in the same commit; that coupling is the intended behaviour, not an oversight.

## 2026-09-28 the Legal Hold sentence gets measured, and three of its four doors turn out to answer for a different reason

Closes the last unclosed half of task #4 -- G10's `内容审核、病毒扫描、投诉和 Legal Hold` row. The complaint
half landed last round; this is the hold half, and it began by noticing that `docs/RIGHTS_POLICY.md:74`,
this file, and the checklist all restated "the flag blocks listing, offer creation and award" without any
run ever having produced one.

**What the flag is: two carriers, not one column.** `moderation_cases.legal_hold`
(`db/migrations/002_creation_asset_market_os.sql:122`) and `rights_manifests.manifest->>'legal_hold'`.
Only `POST /assets/{id}/rights-review` writes the latter, and it cannot write it alone --
`services/api/app/routers/assets.py:174-177` sets seven capabilities to `blocked` in the same statement.
That single fact is what makes three documented predicates unreachable, and `scripts/hold_drill.py` proves
each unreachability with an arm rather than an argument:

1. `rights_manifests` refuses UPDATE (`immutable_rights_manifests`, `db/migrations/001_production_candidate.sql:467`),
   so a pinned manifest can never be flipped after the fact -- the drill issues the UPDATE and matches
   `rights_manifests is immutable`.
2. Every review **adds a version**, and both market functions test "is my pinned manifest still newest"
   **before** they test the hold (`002:673-674`, `009:25`). A hold applied after an offer therefore answers
   `offer rights manifest is stale`, and the drill pins that exact sentence instead of the one the docs
   claimed -- two clauses masking each other is exactly the failure mode this repo has been bitten by before.
3. On the creation side the same shape recurs: `commerce.py:46` tests capabilities, `commerce.py:48` tests the
   flag, and the door never produces "flag set, capability allowed". So the flag disjunct has no producer
   either. Measured consequence: **zero** rows join `asset_offers` to a held manifest anywhere in the database,
   which the drill asserts as a stack-wide invariant -- which is why `002:568` is recorded as defence in
   depth rather than as a live wall.

**What is live, and it is the case half.** `002:680` refuses `asset is restricted`, `004:30`/`009:31` refuse
`submission asset is restricted`, both matched verbatim. The release arm for each does not merely check the
absence of a word: `prepare_brand_award` must **return its row** (`count = 1`) after dismissal, and the
purchase route must actually pass, so a fixture that was never valid cannot be reported as a working guard.
The predicate has two halves and is tested as two: clearing `legal_hold` while the case stays `open` keeps the
offer hidden (`002:574` is flag OR status), which the previous prose in three files blurred.

**What it does not touch, each with its premise asserted first.** Delivered export still returns a real zip;
an issued licence stays `active`; the seller's own `/api/assets/{id}/export` is unaffected; the split rows
count the same before and after -- and the drill requires that count to be **non-zero first**, because
"nothing changed" reads identically when there was nothing there. Correcting that last door's pointer turned
up a stale citation in three files: `main.py:1183` is now `moderation_report_link`; the export route is
`:1230`. All three faces were re-read from the tree and re-pointed.

**The append-only half: one guard works, one does not exist.** `rights_evidence` and `licenses` refuse DELETE
even for `music_admin` (`rolsuper=t`), and `media_assets` has no DELETE trigger -- proven by a three-step
probe on an unreferenced row: first show the session's triggers are armed (the same table refuses an
unsupported `scan_status` write, `a clean media asset must name the engine that cleared it`), then show the
DELETE lands, then show the row is gone. The gap stays registered in §待属主定值 item 6 rather than being
closed by a guessed guard.

**Readings.** `scripts/hold_drill.py` **42/42** on the live stack with the commercial overlay -- the six freeze arms of item 31 make it 48/48, and 48/48 is what the current authority archives; it is chain
step 19 (`hold-drill`, after `market-reconciliation`) and a step in CI's `commercial-flow` job, with
`tests/unit/test_ci_covers_chain_payloads.py` keeping the two dispatch lists from diverging. It needed a
precondition stated rather than assumed: three earlier runs of this drill spent the demo workspace's credits
and it reddened as `402 available=0.0, required=10`, so it now tops up through the platform's own
`/orders/credits` door via `e2e_client.ensure_credits` (`scripts/e2e_client.py:110`) instead of duplicating
that logic. `tests/unit/test_legal_hold_surface.py` adds 8 resident cases (denominator by symmetric
difference over the SQL reader set, the live `prepare_brand_award` body being 009's qualified rewrite, the
two refusal sentences verbatim, and the negative doors with a `credit_holds` control so the bare word `hold`
cannot silently widen or narrow the match); its must-fire control plants a read into a view that does not
read the flag today and requires the ruler to report exactly that one line in both directions. Ladder
**403 passed**, 40 files. Two harness bugs surfaced on the way and are fixed in the drill: `POST /jobs`
answers **202** (a 200/201-only fixture guard reads success as refusal), and `sql_err()` took the **last**
stderr line, which is `CONTEXT: PL/pgSQL function ... at RAISE` -- it names a function, not the platform's
refusal, and it reddened nine checks at once before the ERROR line was used.

**An environment cause, registered with its readings.** Two chain runs were spent finding this one.
`acceptance-20260927T221550Z` died at step 2 because the gateway's pinned address sat inside the range the
allocator fills first -- fixed, and registered above. `acceptance-20260927T224109Z` then died at step 11 with
the erasure drill reporting `invalid or expired access token` for a token that had passed the same dependency
one second earlier. The cause is not the auth code: the api container's clock read
`2026-09-27 22:59:09` (`exported_at` in a response body) while the host read `2026-09-28 05:05:05` -- a
6h06m lag, i.e. the Docker VM's clock after a host sleep -- and when such a clock is stepped forward
mid-run, `exp` on a freshly minted token is instantly in the past. Both reds are kept.
`step_stack_up` now asserts the precondition it exposed (host vs `api` and `postgres` wall clocks, failing
above 120s with the seconds printed), and `erasure_drill.scalar()` returns `(no rows)` instead of raising
`IndexError` -- the traceback had been naming neither the query nor the fact that nothing matched, which is
how a clock artifact reached the log looking like an authentication defect. Measured after the fix, on a
recreated stack: `clock skew vs host: api 0s`, `postgres 1s`.

**Not yet certified.** No authoritative chain run includes step 19 or these 403 tests yet; the four faces
still describe `037b818`. The next `acceptance-all.sh` run is what restamps them, and it will carry the
address-dimension round above as well.

## 2026-09-28 the certification that closes the gateway defect

Authoritative run `acceptance-20260928T065139Z` (commit `ef88d14`, fresh database recorded as `fresh_database=1` in the same header, 2026-09-28T06:51:39Z → 2026-09-28T07:07:49Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "6.34 7.69 11.67" on a 4-cpu Docker VM with `disk_free_kb=5634660` beside it), browser record `release-evidence/browser-a11y-20260928T070328Z/report.json`.
This is the first archived chain that carries the Legal Hold drill at all (`hold-drill` row 19, 42/42), and
the first whose second-factor drill archives 79/79 -- the two checks that had only ever been read on a
running stack. It is also the run that proves the gateway fix in place rather than on a bench: step 8's
restore recreates the application tier, and the step's new precondition reads
`经由网关（127.0.0.1:18080）的 /health 是 200（等了 0 秒）`; step 12's gateway login, which was the 502 that
stopped the previous run, is green inside the same drill that found the defect. Step 1 read 423 unit tests
over 42 files, the manifest covered 246 tracked sources, and the authority matrix still agrees with the
code at 100 routes / 54 writes.

**Still not certified, unchanged by this run:** the 500-concurrency gate. Row 23 is skipped unless
`CAPACITY=1`, and on this host (`docker_cpus=4`) the gate measures red, which is why the switch is off by
default rather than the red being filed as a pass.

**What the stamping tool still cannot do.** Two figure families in `docs/TEST_REPORT.md` remain hand-appended:
the per-run latency series for `provider-regression-100` (fourteen readings, counted on the face itself) and for
`generic-rest-roundtrip` (eleven), together with the counts of green runs that carry them. Their denominator
is "which archived runs actually ran that step, and with what timings", and those numbers live only in the
per-step logs, which `.gitignore` keeps off the tree -- so they cannot be recomputed from the tracked archive
the way the commit sequence and the red census can. They are appended by hand from the current run's log
(`p50 4.095s / p95 6.43s` and `4.08s / 5.22s` for `acceptance-20260928T111210Z`), and the sentences say so
("计数会跨跑传，耗时不会").

**HEAD vs. the certified tree, measured rather than asserted.** This paragraph has been rewritten twice, and
each time the version it replaced had said "the current authority" in the present tense after the authority had
already moved. The reading taken for the authority these faces now certify, `acceptance-20260928T111210Z`
(commit `d98e650`), is `git diff --name-only d98e650..0df3a00` → 14 paths, and the same command with the
product pathspec (`services db shared docker-compose.yml docker-compose.commercial-test.yml Makefile checks`)
→ 0 paths: what moved between the tested tree and the tree these corrections land on is six faces, three
evidence files, the stamper, this test file and the manifest. The first draft of this sentence read
`d98e650..HEAD` → 0 paths and justified it by "HEAD *is* the certified commit" -- the commit carrying that
sentence refuted it inside a minute, which is the same moving-endpoint defect the sentence existed to close,
caught by the sentence itself. Ranges are written between two
commits from here on, never against `HEAD` -- the same command one round earlier reads `1dbf99e..a841628` → 15
paths and `1dbf99e..d98e650` → 24, all of them faces, evidence, the stamping and census scripts, the
release-record gates and the manifest, and a sentence naming a moving endpoint cannot tell those readings
apart. The ladder: that run's step 1 reported 437, and the tree these corrections land on collects 442 -- the
difference is the five gates this close-out added. The rule that made
this paragraph necessary still holds: the faces quote the *run's* readings, so a test added after a run is
not that run's number until a run is made with that test in it.

## 2026-09-28 two reds that were about the machine, and the port that was never ours

`acceptance-20260928T051817Z` and `acceptance-20260928T054641Z` both failed on things the code was right
about. The first: one candidate of a generation job was judged permanently bad because `ffprobe` did not get
scheduled in time (`TimeoutExpired: ... timed out after 15 seconds`, `attempt_count` stopped at **1**, host
load 11.5 rising to 25 while eight CPUs were shared with my own unit ladder). The worker's `except Exception`
wrapped every probe outcome as a content failure, so the retry path never saw it. Fixed by naming the line
where the two classes actually differ -- `services/worker/validation.py:validation_is_infrastructure`
(`TimeoutExpired` and `FileNotFoundError` are the machine, `CalledProcessError` is the file) -- wired at
`services/worker/worker.py`, and pinned by `tests/unit/test_validation_retry.py`, whose AST arm asserts an
`If` gated on the verdict and raising `Retryable`, because "the file mentions the function" is not a wiring
assertion. The timeout was **not** raised: 15 idle seconds is the host's bill, and 60 seconds would only
reappear later and uglier.

The second was worse, because the stack lied about being healthy. Step 8's `restore.sh` recreates the
application tier; a recreated container gets a new address from Docker's IPAM, and the gateway's
`upstream api_upstream { server api:8000; }` resolves that name **once, at config load**. Its error log says
it dialled `172.28.95.8:8000` while `docker inspect` had api at `.7` and worker at `.8` -- the pool had given
the api's old address to somebody else, and nothing there listens on 8000. Every route behind the gateway was
502 from then on; the gateway's own healthcheck asks a local `/gateway-health`, so `docker compose ps`
reported it healthy for the rest of the run. Step 12's mfa drill, the only leg in the chain that posts
through the gateway, took the hit (`status 502`), and the following session-count check read `5 -> 6` because
that 502 minted one session short -- one cause, two faces.

Two of my three reproduction attempts were void, and both for instrument reasons worth recording: the host's
**8080 belongs to another project** (`curl :8080/openapi.json` returns `"title":"企业采购自动询价 Web 系统 API"`),
because this stack publishes the gateway on `GATEWAY_PORT=18080` from `.env` while `.env.example` still
advertises 8080 -- so the "it works now" readings that made me call this a race were someone else's app. The
second arm recreated the api and got back the *same* IP, so its green was a no-op. Only a third arm, which
pins the old slot with `docker run --ip` before recreating, moved the api `.12 -> .7`: the gateway was still
502 at t+30s while `curl :8000/health` was 200. **Deterministic, not a race.**

Chosen fix (over pinning api/web/admin addresses, the six-dimension notes are in the checklist item 30):
make the gateway follow DNS -- `services/gateway/nginx.conf` now gives each upstream a `zone`, declares
`resolver 127.0.0.11 valid=10s ipv6=off`, and uses `server api:8000 resolve;`. nginx's own doc for `resolve`:
it "monitors changes of the IP addresses that correspond to a domain name of the server, and automatically
modifies the upstream configuration without the need of restarting nginx", and needs a resolver plus a
server group in shared memory; open-source gained it at 1.27.3 and the image reports `nginx/1.27.5`. The repo
already carried this shape in `services/web/nginx.conf:12-18`, whose comment names the identical symptom --
the defect survived only because the fix was never applied to the gateway.

Verified as a pair on one fault state: old config + api moved ⇒ the new step-8 precondition goes red
(`经由网关(127.0.0.1:18080) 的 /health 是 502 而不是 200 … 直接问 api 是 200`); new config + the same moved api
⇒ green, waited 0 s, no gateway restart. Resident: `tests/unit/test_gateway_upstream_resolution.py` censuses
`services/*/nginx.conf` for any proxy that cannot re-resolve, with a denominator (3 configs, the gateway's 3
upstreams) and five firing arms -- drop `resolve`, drop `zone`, drop `resolver`, a `proxy_pass` naming no
declared upstream, and a commented-out old shape which must **not** count. `scripts/smoke.sh` now asks
`docker compose port gateway 80` instead of assuming 8080, and refuses (rc=2) when compose reports nothing,
rather than printing four greens for a neighbouring project.

**Certified by the section above.** `acceptance-20260928T054641Z` stopped at step 12 (the chain is fail-fast),
so it is archived as a judged-red finding, not an authority. The four faces' mechanically-derived cells were
restamped to 22 archived reds / `26-09-28 = 2` by `scripts/release_face_cells.py --apply` while that red was
the newest evidence in the archive; the authority cells then moved to `acceptance-20260928T065139Z` in the
same tool's next pass. Ladder 409 → 423 (7 gateway-census arms + 7 table-resolution arms).

## 2026-09-28 retention stopped meaning only "cannot delete"

`docs/RIGHTS_POLICY.md` promises a Legal Hold keeps every record, and promises an issued licence is frozen
across parties, asset, manifest, template, terms, territory, duration, licensee and hash. What the tree
enforced was narrower: `002:462-463` bound `rights_evidence` and `licenses` with **DELETE-only** triggers, so
the rows could not be removed but their contents could be rewritten. That is the difference between an audit
trail and an editable one.

Freezing `UPDATE` outright would have been a defect, and that is measurable rather than arguable: the product
writes to both tables in exactly two places -- `services/api/app/routers/assets.py:217` (`status`,
`reviewed_by`, `reviewed_at` when a reviewer accepts evidence) and `services/api/app/routers/market.py:372`
(`status='refunded'` on a refund); an exhaustive sweep of `UPDATE (rights_evidence|licenses)` across
`services/`, `scripts/`, `tests/` and `db/migrations/` finds nothing else. So
`db/migrations/022_frozen_evidence_and_license_columns.sql` freezes **columns**, not operations:
`frozen_columns_except()` projects `to_jsonb(NEW)` and `to_jsonb(OLD)` minus an allowed list and refuses if
the remainder differs, naming the column that moved. `rights_evidence` keeps `{status,reviewed_by,reviewed_at}`,
`licenses` keeps `{status,activated_at}` -- which is the policy sentence read as a predicate.

Eight readings on the live stack, both polarities: reviewer write `UPDATE 1`; status write `UPDATE 1`;
`SET evidence=`, `SET submitted_by=`, `SET territory=`, `SET license_hash=`, `SET rights_manifest_id=` each
refused with the offending column named; and `SET session_replication_role=replica` still bypasses, exactly
like the rest of the immutability list -- so this guard is no stronger and no weaker than the ones already
certified. `docker compose --profile test run --rm acceptance` on a clean stack carrying 022: `11 passed`.

Two mistakes the measurement caught before certification, both worth keeping: a trigger function cannot
declare parameters (`migrate` refused to compile it: "the arguments of the trigger can be accessed through
TG_NARGS and TG_ARGV instead"), and `TG_ARGV` is 0-based -- writing `TG_ARGV[1]` left `allowed` NULL, which
made **every** UPDATE raise `FOREACH expression must not be null`, including the two the platform needs. The
must-not-fire arms of the matrix are what said so. Then the matrix itself was vacuous once, on a fresh
database with no evidence or licence rows at all: an early version printed six "PASS" lines that were really
`invalid input syntax for type uuid: ""`, so the script now aborts with rc=3 unless the fixture rows exist.

Resident: `scripts/hold_drill.py` 42 -> **48/48**, the new arms being the five column readings above plus one
that compares `pg_trigger.tgargs` against the expected column list byte for byte -- `tgargs` turned out to be
a single NUL-terminated `bytea` (measured through `information_schema.columns`, after two wrong guesses:
there is no `tgargn` column, and `chr(0)` is not permitted in SQL text), so the assertion is
`tgargs = convert_to(args,'UTF8') || '\x00'::bytea`, and a trigger that lost its argument list would fail
the drill instead of silently refusing everything.

## 2026-09-28 the certification that carries the column freeze

Authoritative run `acceptance-20260928T081806Z` (commit `3dbd175`, `fresh_database=1` in the same header, 2026-09-28T08:18:06Z → 08:35:46Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "6.04 5.17 4.75" on a 4-cpu Docker VM with `disk_free_kb=5680956` beside it), browser record `release-evidence/browser-a11y-20260928T083139Z/report.json`.

It is the first chain certified against a database where `rights_evidence` content and an issued licence's
terms are frozen per column (migration 022), and the drill that measures it archives **48/48** rather than
the 42/42 of the previous authority. Step 1 read 423 unit tests over 42 test files, the manifest covered
247 tracked sources, the authority matrix still agrees at 100 routes / 54 writes, and the run again recorded
`mfa drill: 79/79`, `member drill: 98/98`, `erasure drill: 53/53`, `report drill: 50/50`,
`media scan drill: 14/14`, `market reconciliation: 15/15`, `restore fidelity: 2 workspaces, 2 assets
byte-identical, ledgers unchanged`, `8 jobs, 2 workers, 160.0 credits settled once`.

Both latency series moved, and they move in opposite directions this round, which is the reason the record
quotes every reading instead of a mean: `provider-regression-100` came in at 100/100 with
p50 4.15s / p95 8.38s (fourteen green runs carrying it), while `generic-rest-roundtrip` came in at 25/25
with p50 8.6s / p95 20.91s (fifteen) -- the slow shape of a co-tenant window, not a different error rate
(0.0% both). The accessibility leg held: 118 view records, 92 axe scans, `violations_by_impact` empty,
no uncaught errors, and the hidden-element census now at 1285 scanned with 0 still rendering.

The gateway leg is certified by this run too: step 8's precondition reported
`经由网关（127.0.0.1:18080）的 /health 是 200（等了 0 秒）` immediately after the restore recreated the
application tier, and step 12's login-through-the-gateway check passed inside the 79/79 -- the check whose
502 ended the previous attempt at this certification.

**Not certified by any run here:** the 500-concurrency gate (row 23, skipped unless `CAPACITY=1`; red on a
4-cpu VM), the real-provider contract evidence, and the two owner decisions in `待属主定值` (3: whether one
browser may hold both surfaces, now measured; 6: whose hold counts, plus the operator-only
`media_assets` delete guard).

## 2026-09-28 the certification that puts the browser leg's console figures under the stamper

Authoritative run `acceptance-20260928T084412Z` (commit `1dbf99e`, `fresh_database=1` in the same header, 2026-09-28T08:44:12Z → 09:00:20Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "3.64 4.99 6.26" on a 4-cpu Docker VM with `disk_free_kb=5410160` beside it), browser record `release-evidence/browser-a11y-20260928T085606Z/report.json`.

`git diff --name-only 3dbd175..1dbf99e` is eight paths and none of them is product code or a migration: four
faces, the manifest, the previous round's two evidence files, and `tests/unit/test_release_record_consistency.py`
(the quoted-window clause plus its four controls). So this section exists for one reason: it is the run whose
`report.json` the six new browser cells read. Step 1 recorded 428 unit tests,
the manifest again covered 247 tracked sources at 100 routes / 54 writes, and the legs repeated
`mfa drill: 79/79`, `member drill: 98/98`, `erasure drill: 53/53`, `report drill: 50/50`,
`hold drill: 48/48`, `media scan drill: 14/14`, `market reconciliation: 15/15`,
`restore fidelity: 2 workspaces, 2 assets byte-identical, ledgers unchanged`,
`8 jobs, 2 workers, 160.0 credits settled once`; the two latency series came in at 100/100 with
p50 4.09s / p95 5.22s (fifteenth green) and 25/25 with p50 4.13s / p95 4.15s (sixteenth), and the
accessibility leg at 118 view records / 92 axe scans with an empty `violations_by_impact`, 1286 hidden
elements scanned and 0 still rendering.

Two things surfaced while wiring those cells, and both are defects in the record rather than in the product.

1. **The reader paired a certified run with the wrong browser report.** It globbed
   `browser-a11y-<the run's date>*/report.json` and took the last one, which is whatever a11y leg happened
   to be newest that day. 2026-09-28 carried several a11y legs for several runs -- `070328Z`→`065139Z`,
   `083139Z`→`081806Z`, `085606Z`→`084412Z` -- so the faces could -- and did --
   read a hidden-element census and a `git_commit` off a report the certified run never produced. The rule
   is now two keys at once: the report's `git_commit` must equal the one the SUMMARY records, and its
   `generated_at` must fall inside that run's own `browser-a11y` row window. Nothing satisfying both reads
   as "no report" and the stamping round refuses; `test_the_browser_report_is_paired_by_commit_and_window`
   plants a same-day newer report on another tree and requires the pair to ignore it.
2. **The sentence about console lines was counting one axis and naming another.** It read the report's
   `sorted(set(...))` of `"<label>: <text>"` as a count of refusals and attributed the six 403 lines to
   three walks, one of which never sent a request. The server-side census for this run's own window
   (`scripts/server_refusal_census.py`, reading `docker compose logs api` over the row's stamps) answers
   48 4xx events over 8 endpoints -- 28 of them `GET /api/auth/me` -- so six console lines stand for
   eighteen refused requests, and `DELETE /api/workspace/members/…` appears exactly twice, both of them the
   correct-email removals: the mismatched-email confirm is refused in the page, not by the API. The line
   counts, the status split, and the per-status label-shape counts are cells now; the refusal counts are
   cited prose until a chain whose report carries `refusal_counts` becomes the authority, because a cell
   reading a field that report does not have would stop the whole round.

The gate that compares the two observers is `scripts/server_refusal_census.py --self-test`, and its arm count
is the tool's own printout -- at that round the run printed 7, which is what
`git show a841628:scripts/server_refusal_census.py` still shows as seven `arms.append` lines, while
`scripts/static-verify.sh` now runs that self-test inside step 1 and refuses the whole step if any arm fails,
so no sentence has to keep the number current. Its arms were then: the honest pair, a refusal the gate missed, one event short, an injected route that actually reached the api, unparsable
lines, an empty window, and both docker line shapes. The browser leg grew the same shape of arm in its
own `--self-test`: a planted 403 must appear in `refusals` and a 200 on the next request must not, proved
by removing the listener and watching the arm go red (rc=1, message named) before restoring the file
byte-identically.

## 2026-09-28 the certification that carries the refusal census

Authoritative run `acceptance-20260928T095607Z` (commit `a841628`, `fresh_database=1` in the same header, 2026-09-28T09:56:07Z → 10:14:30Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "7.99 14.17 18.71" on a 4-cpu Docker VM with `disk_free_kb=5280940` beside it), browser record `release-evidence/browser-a11y-20260928T100927Z/report.json`, cross-check `release-evidence/browser-a11y-20260928T100927Z/server-refusals.txt`.

This is the first certified run whose a11y report carries the per-endpoint refusal census, which is the
precondition the two observers needed in order to be compared instead of asserted: the gate recorded 52 4xx
events over 9 endpoints while `docker compose logs api` over that row's own window answers 50 over 8, and the
whole difference is the injected `GET /api/admin/v12/moderation -> 500` pair, which the reconciliation
requires to appear on the browser side and not on the server side. Console lines are 24 of those 52 events --
that ratio is the reason the record stopped calling lines "refusals". Step 1 read 434 tests, the manifest
covered 248 tracked sources, the authority matrix still agrees at 100 routes / 54 writes, and the legs
repeated `mfa 79/79`, `member 98/98`, `erasure 53/53`, `report 50/50`, `hold 48/48`, `media scan 14/14`,
`market reconciliation 15/15`, `restore fidelity: 2 workspaces, 2 assets byte-identical, ledgers unchanged`,
`8 jobs, 2 workers, 160.0 credits settled once`; provider-regression came in 100/100 at p50 4.555s /
p95 5.65s and generic-rest 25/25 at p50 4.19s / p95 4.27s.

Three prose habits were retired by measurement in this run's own tree, not by re-typing them:

- the hand-counted ordinals ("the fourteenth green run with this step", "the fifteenth") are gone; the
  counts are read off SUMMARY rows -- 23 tracked all-green runs carry a PASS `provider-regression-100`, 18
  carry `generic-rest-roundtrip`, 21 carry `browser-a11y`, 4 carry `hold-drill` -- and the faces now say
  "在册带这一步的绿线运行已有 N 次" as stamped cells;
- six figures that were copies of a tracked artifact's numbers (96 routes / 51 writes, 86 and 79 OpenAPI
  paths, 89/48 on the checklist, and a line claiming `securitySchemes` is empty) are compared against
  `shared/contracts/authority-matrix.json` and `shared/contracts/openapi-v13.json` by `contract_problems`;
- a sentence in this file's own head claimed the browser record carried "the same commit" while pointing at
  a report from a different tree. `pairing_problems` now refuses any sentence that claims a shared commit
  without naming a run stamp or a commit token it can be compared against, so the claim has to be falsifiable
  to be written at all.

## 2026-09-28 the first authority whose own step wrote the cross-check

Authoritative run `acceptance-20260928T111210Z` (commit `d98e650`, `fresh_database=1` in the same header, 2026-09-28T11:12:10Z → 11:36:56Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "3.46 5.48 10.38" on a 4-cpu Docker VM with `disk_free_kb=11476052` beside it), browser record `release-evidence/browser-a11y-20260928T113020Z/report.json`, cross-check `release-evidence/browser-a11y-20260928T113020Z/server-refusals.txt`.

What is new is not a reading but who produces it: row 22 now writes the server-side census itself, so the
artifact a resident test demands is made by the pipeline rather than by someone remembering a follow-up. On
this run the two observers agree on the same shape they did before -- gate 52 refusals over 9 endpoints, api
log 50 over 8 in the window around the gate, 401 x30 / 403 x20 / 500 x0 on the server side, and the two
injected moderation responses present only where they were fulfilled. Console lines are 24 of those 52
events; step 1 read 437 tests; the manifest covered 248 tracked sources at 100 routes / 54 writes; and the
legs repeated `mfa 79/79`, `member 98/98`, `erasure 53/53`, `report 50/50`, `hold 48/48`,
`media scan 14/14`, `market reconciliation 15/15`, `restore fidelity: 2 workspaces, 2 assets
byte-identical, ledgers unchanged`, `8 jobs, 2 workers, 160.0 credits settled once`, with provider-regression
100/100 at p50 4.095s / p95 6.43s and generic-rest 25/25 at p50 4.08s / p95 5.22s. The accessibility leg
held at 118 view records / 92 axe scans, an empty `violations_by_impact`, 1287 hidden elements scanned and
0 still rendering.

Two reds precede this authority and are kept as tracked evidence, because both were about the tooling rather
than the product: `acceptance-20260928T103425Z` (row 22 FAIL with the gate itself passing -- the census was
asked for the row's window while the row did not exist yet, 附带发现第 34 条) and
`acceptance-20260928T110502Z` (row 1 FAIL in one second on `mismatched=2`, the manifest not rewritten after
two doc edits). The chain's own preflight also refused one start on free space
(`free_kb=3954532 < 4194304`); reclaiming only dangling images lifted it to the 11476316 KB that `acceptance-20260928T110502Z`'s own header
records (the certified run started later the same day at 11476052 KB), and touched no
volume, container, or tagged image from anything else on this machine.

## 2026-09-28 the after-certification read-through: sixteen refutations, and a gate that could not see its own case

The authority above was certified at `d98e650`; then a read-only pass re-opened every figure in the six faces
against the three tracked artifacts (`SUMMARY.txt`, `report.json`, `server-refusals.txt`) and against
`git log` / `git ls-files`. It returned sixteen discrepancies. Each was re-measured here by hand before the
sentence moved, and all sixteen were about the record rather than the product:

- **Ordinals the archive moved** when two more runs were committed (`acceptance-20260928T093445Z`, the chain
  that skipped the browser leg, and `acceptance-20260928T110502Z`, the dirty-tree refusal). The faces now read
  24 prior green runs, 24 judged-red SUMMARYs, `26-09-28 = 4`; the pairing census reads 25 green, 23 carrying a
  `browser-a11y` row, 22 pairing to exactly one report, 1 with the row but `SKIPPED`, 2 from before the step
  existed. The counts are cells, so `--apply` moved them; the sentence in `docs/RELEASE_CHECKLIST.md` 第 32 条
  that had hand-quoted the old census was rewritten to match the same measurement.
- **Four citations that had drifted**: `browser_a11y.py:1385` → `:1386` (the definition, not the blank line
  above it); `services/web/app.js:2075-2089` → `:2077-2078` and `:2090-2091` (the two comments the sentence
  describes); "两个端点各两次" → `/api/admin/v12/workspaces` 三次 plus `/api/admin/v12/payouts` 两次 (the census
  row says so); and the walkthrough states, which had added 4+8+5 and called the total "从九个变成十二个" --
  `team_states` has been twelve since the leg of `20260926T192431Z` and no tracked leg ever held nine.
- **Two figures nobody owned**: the changelog's `浏览器结果 …/report.json` pointer, which every stamp had
  walked past because no cell matched its wording, and `docs/TEST_REPORT.md`'s "派生 96 条 /api 路由、51 条
  写操作", four commits behind the contract. The contract-quote gate read the walkthrough's phrasing but not
  this one, so it now reads both.
- **Two claims with a moving endpoint**: "这一天有三条链各带一次 a11y" (six by the close of the day, all six
  resolved by `browser_pair`), and `git diff --name-only 1dbf99e..HEAD → 15 paths` -- against `a841628` that is
  15, against `d98e650` it is 24, and `d98e650..HEAD` is 0 because HEAD *is* the certified commit. The
  sentences now name two commits each.
- **One reading no artifact carries**: the disk figure `11476340 KB`; the run that started after the prune
  records `disk_free_kb=11476316`.
- **One markdown self-injury**: row 22 of the runbook's step table carried a stray `|`, splitting a
  four-column row into five -- in the very row this session had just widened.

Three resident gates came out of it, all in `tests/unit/test_release_record_consistency.py`: the
authority-line pointer clause (`authority_pointer_problems` -- six citations read on authority lines today, one
unattributable pre-window report, ratcheted), the runbook step-table shape clause (23 data rows, one shape),
and the second contract-quote phrasing. The pointer clause is worth the mistake it grew from: the first version
was *sentence*-scoped and its control was a fixture line I had written myself, so the control passed while the
shipped changelog stayed invisible to the clause -- that file keeps its browser pointer in the last `；`-split
clause of a parenthetical that never names the run beside it. Scope had to widen to the line, and the control
had to become a live tamper of the real face, before the gate could see the defect it was written for.

## 2026-09-28 the steps state their own readings, and a 22nd-row red is kept as evidence

Authoritative run `acceptance-20260928T173456Z` (commit `c9e3cbd`, `fresh_database=1` in the same header, 2026-09-28T17:34:56Z → 17:57:01Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "24.70 39.63 36.48" on a 4-cpu Docker VM with `disk_free_kb=10329264` beside it), browser record `release-evidence/browser-a11y-20260928T175139Z/report.json`, cross-check `release-evidence/browser-a11y-20260928T175139Z/server-refusals.txt`.

The header load above is what the pairing rule reads; the machine was busier by the finish
("25.65 27.19 27.70", the footer line of the same `SUMMARY.txt`), which is why the leg's own window runs
17:51:32Z → 17:57:01Z and the browser step alone took 5m29s.

**What changed is who produces the numbers.** Ten producers now print their own totals on stdout
(`scripts/metric_line.py`), `run_step` copies the last such line into `SUMMARY.txt` as a `metrics <step> k=v`
line -- never a fifth column, because the four-field row shape is itself a guard -- and the reader maps those
keys through `METRIC_FIGURES`, with an absent key published as `NOT-FOUND` rather than as zero. This run is
where that round trip first met a real chain, and it agrees with the log-derived reading at every point the
two can be compared: `unit_passed=445`, `mfa-drill 79/79`, `member-drill 98/98`, `erasure-drill 53/53`,
`report-drill 50/50`, `hold-drill 48/48`, `media-scan-drill 14/14`, `market-reconciliation 15/15`,
`lease-contention credits_settled=160.0 jobs=8 workers=2`, `restore-fidelity-compare assets=2 workspaces=2`,
`provider-regression-100 p50_s=5.975 p95_s=10.03`, `generic-rest-roundtrip jobs=25 p50_s=4.32 p95_s=5.53`.
Eight of those were previously copied by hand out of a per-step log that is not version-controlled; the
latency pair and the drill totals are cells now.

**The leg held, and the two observers agreed exactly.** 118 view records over desktop 1440 and mobile 390,
92 axe scans, `violations_by_impact` empty, 1291 hidden elements scanned with 0 still rendering, 59 mobile
fit checks, export bytes 2731 / 2730. The gate saw 54 refusal events -- 52 of them 4xx and the two
`GET /api/admin/v12/moderation -> 500` responses it fulfils itself -- over 9 endpoints, deduplicated to 34
lines and 24 console lines; the api log in that window answered 52 4xx over 8 endpoints (401 x30, 403 x22,
500 x0) out of 526 request lines, all 526 parsed.

**One census label was wrong until this round, and the fix is measurable.** `server_4xx_events` /
`gate_4xx_events` summed every status `>= 400`, so the gate's number counted its own injected 500s inside a
figure the faces quote as "次 4xx" (it read 54 against the server's 52). The two lines now sum 400-499 and
each observer's 5xx sits on its own line, with two new self-test arms (11 total). Re-deriving this run's
artifact with the corrected ruler changed nothing but those header lines --
`diff` on the file before and after shows `gate_4xx_events: 54` replaced by `server_5xx_events: 0`,
`gate_4xx_events: 52` and `gate_5xx_events: 2`, with `log_lines_read`, `request_lines_parsed`,
`server_4xx_events` and all eight STATUS rows byte-identical -- which is also the proof that the two
observers agree at 52 = 52 once the axes are the same class.

**The red immediately before this authority is now tracked, and stays red.**
`acceptance-20260928T163801Z` (commit `ceea166`, `fresh_database=1`,
`browser-a11y | FAIL | 16:52:46Z → 16:58:29Z`, the other 21 rows PASS) failed because the census would not
write an artifact whose observers disagree: 517 api log lines all parsed, `GET /api/auth/me -> 401` 27 times
server-side against 26 in `release-evidence/browser-a11y-20260928T165253Z/report.json`. The mechanism is not
proven -- the per-event `refusal_timeline` that could tell a post-close response from an unseen one landed in
`c9e3cbd`, after that run, and its report has no such key -- it did not reproduce on the same commit
(`browser-a11y-20260928T170452Z`, window 17:04:52Z → 17:09:57Z: 49 4xx on both sides, terminal reading only,
no artifact because it was run without `--write`), and no tolerance was added to the count comparison.
Full write-up: `docs/RELEASE_CHECKLIST.md`「本轮附带发现」第 36 条.

**Ladder.** The certified run read 445 collected instances; writing this section added
`tests/unit/test_reference_doc_figures.py` (8 cases: the compose census, the CI job list, the contract
census, a citation scan of the seven reference docs outside the faces, eight pinned `file:line` pointers, and
constructed-boundary fire controls), and `pytest -q tests/unit` on this tree reads 453 passed -- a dated
reading, not a cell, since the stamped `unit_passed` belongs to the run above.

## 2026-09-28 the chain survives a step that states nothing, and the record stops hand-copying

Authoritative run `acceptance-20260928T184730Z` (commit `c007b21`, `fresh_database=1` in the same header, 2026-09-28T18:47:30Z → 19:04:20Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "7.43 7.61 9.53" on a 4-cpu Docker VM with `disk_free_kb=10333140` beside it), browser record `release-evidence/browser-a11y-20260928T185947Z/report.json`, cross-check `release-evidence/browser-a11y-20260928T185947Z/server-refusals.txt`.

**This is the run 附带发现第 37 条 asked for.** The previous attempt died after row 2 because a step that
states no readings made `grep '^metric '` return 1, and `set -euo pipefail` turned that into the step's own
exit. Here all 23 rows are written, `finished_at` is present, and row 1 carries BOTH readings the step
emitted -- `metrics static-verify refusal_census_selftest_arms=11` and `metrics static-verify
unit_passed=456` -- which is the multi-line copy rule working in production rather than in a fixture, and the
stray `metrics static-verify a=1` (a self-test's own sample) is gone because the arm builds its producer line
from `PRODUCER_PREFIX` instead of calling the printing function. Thirteen `metrics` rows in total.

**The two observers now sit on one axis.** The api answered 49 4xx over 8 endpoints (401 x30, 403 x19, 500
x0) inside 505 request lines, all 505 parsed, while the gate recorded 51 events over 9 endpoints: the same 49
plus the two `moderation` 500s it fulfils itself. Before the label fix those two numbers read 52 vs 54 and
were not comparable at all. The leg held 118 view records / 92 axe scans with
`critical / serious / moderate 三档全零` -- that sentence is now a stamp over `violations_by_impact`, not a
transcription -- 1286 hidden elements scanned and 0 still rendering, and 24 console lines.

**What the faces are allowed to say.** 92 cells across the six faces, all resolved against this run. The
drill counts on `docs/TEST_REPORT.md` (member 98/98, report 50/50, erasure 53/53) and both regression lines
(`100/100 completed, error rate 0.0%` at p50 4.1s / p95 5.56s, `25/25 completed, error rate 0.0%, settled 250
credits` at p50 4.16s / p95 5.2s) now take their step ordinals and job counts from the SUMMARY too, as does
the G9 gate's "实跑 100 个任务". `test_every_published_reading_is_quoted_or_accounted_for` requires every
key the reader publishes to be either interpolated by a cell or classified with a reason, so a reading can no
longer be published into silence; `refusal_census_selftest_arms` is the one key that stays classified rather
than quoted until a sentence wants it.

**Ownership outside the faces.** `tests/unit/test_reference_doc_figures.py` grew to 12 cases and now
recomputes `docs/CODE_WALKTHROUGH.md`'s seven `（N 行）` claims (six were wrong: the API entry was listed at
465 lines against 1295, the worker at 408 against 504) and compares the runbook's host-port table against
compose with `${VAR:-default}` resolved -- 11 = 11, after a first pass of that rule misread two of the
eleven because the interpolation contains a colon. Whole-tree counts (`git ls-files` 347, `scripts/` 42,
`db/migrations` 22) are date-labelled instead of gated, because gating them would redden every commit that
adds a file and that tax would end with the rule being deleted.

**Ladder.** 456 collected and passing at `c007b21` -- 445 at the previous authority. The face quotes 456
because that is what the certified run measured, and the stamped tail of `docs/TEST_REPORT.md`'s series is
the cell that says so. The tree this section is written in reads higher: `pytest -q tests/unit` now prints
464 passed and `scripts/static-verify.sh` exits 0. The eight cases between the two readings are six in
`tests/unit/test_reference_doc_figures.py` -- the line-count rule and its fire control, the port table and its
fire control, the case-count rule and its fire control -- and two in
`test_release_record_consistency.py` for the verdict marker. That last number is a reading of this moment,
not a claim about the record: any added case moves it, which is exactly why the authority figure lives in a
cell and this one comes with the command that produced it.

## 2026-09-28 the third refusal-census red, and the axis that finally attributes it

Authoritative run `acceptance-20260929T015624Z` (commit `83f19ad`, `fresh_database=1` in the same header, 2026-09-29T01:56:24Z → 2026-09-29T02:13:18Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "5.48 4.76 4.26" on a 4-cpu Docker VM with `disk_free_kb=9208916` beside it), browser record `release-evidence/browser-a11y-20260929T020903Z/report.json`, cross-check `release-evidence/browser-a11y-20260929T020903Z/server-refusals.txt` -- server 4xx 50 against gate 4xx 50, `gate_issued_events: 520`, `gate_aborted_events: 10`, `unsettled_at_close` empty; step 1 reads `unit_passed=481`, `refusal_census_selftest_arms=25`. It is the first run certified on a picker that refuses a chain which never wrote its `finished_at=` line.

Seven earlier runs of the same fix -- `acceptance-20260928T211437Z` (commit `c469bf3`, browser record `release-evidence/browser-a11y-20260928T212652Z/report.json`, server 4xx 48 against gate 4xx 48) and `acceptance-20260928T220607Z` (commit `e75dcc6`, browser record `release-evidence/browser-a11y-20260928T221814Z/report.json`, server 4xx 51 against gate 4xx 51) and `acceptance-20260928T230940Z` (commit `01147cf`, browser record `release-evidence/browser-a11y-20260928T232127Z/report.json`, server 4xx 48 against gate 4xx 48, `gate_issued_events: 517`), plus `acceptance-20260928T235629Z` (commit `8f69a1e`, browser record `release-evidence/browser-a11y-20260929T000909Z/report.json`, server 4xx 49 against gate 4xx 49, `gate_issued_events: 519`), plus `acceptance-20260929T003307Z` (commit `fa64cef`, browser record `release-evidence/browser-a11y-20260929T004508Z/report.json`, server 4xx 49 against gate 4xx 49, `gate_issued_events: 519`), plus `acceptance-20260929T010111Z` (commit `09be2ab`, browser record `release-evidence/browser-a11y-20260929T011402Z/report.json`, server 4xx 48 against gate 4xx 48, `gate_issued_events: 517`), plus `acceptance-20260929T013232Z` (commit `16e2660`, browser record `release-evidence/browser-a11y-20260929T014406Z/report.json`, server 4xx 49 against gate 4xx 49, `gate_issued_events: 520`) -- are kept here because the desktop-only legs cited below were taken between them.

That host pair read "5.57 6.10 5.60" when the chain wrote its `finished_at=` line, i.e. this time the machine was **busier** at the end than at its start (5.48 -> 5.57 on the one-minute average) -- stated here because the load a round is judged under is the one printed beside its own run on the line above, and because the earlier rounds this section keeps were all quieter at the end than at the start, so "the tail is always calmer" is not a law, only what those runs happened to measure.

`acceptance-20260928T200930Z` row 22 failed with `the server answered 27x GET /api/auth/me -> 401 but the
gate recorded 26`, and the sentence I had written from its own diagnostic -- "the gate was alive at that
instant, therefore teardown is refuted and a blind page is the live hypothesis" -- was withdrawn this round.
The print it rested on, `surplus_times()`, returns the newest N of that tuple's server stamps; it never said
which stamp went unpaired. Aligning the 27 server stamps against the 26 gate events monotonically (±250 ms)
matched 26 of 27 and left the last one ambiguous between two neighbours, because the inter-observer offset
itself ranges −31.6 … +142.6 ms (median −9.5). Milliseconds were no better than seconds at this question.

So the fix is a witness taken before an answer can hide (`c469bf3`), not a better clock:

- `request_counts` -- every request the armed pages put on the wire, including this process's own `api_post`
  calls, judged as `server lines ⊆ issued` per endpoint, restricted to endpoints that refused at least once
  so the health check's `GET /ready` and Prometheus' `GET /metrics` (both in the same window, neither the
  gate's to observe) cannot make it red. In the red window `/api/auth/me` reads 27 issued against 27 logged:
  the missing event was a response, not an observer.
- `abort_timeline` -- the requests that got no answer at all. The apps' own `logout()` handlers await the
  request and then call `location.reload()` (`services/web/app.js:413`, `services/admin/admin.js:235`), so
  five per viewport-leg end in `net::ERR_ABORTED` while the api still answers 204 and logs it. That is the
  class the response axis cannot see, now named instead of argued.
- `settle_context()` -- each context waits for networkidle before it closes, which removes the blind window
  rather than tolerating it; a page that never goes idle is published in `unsettled_at_close` by name.

The comparison itself is untouched: still `server ⊆ gate`, still no ±1. The only exemption is the run's own
named reading -- an empty or absent `unsettled_at_close` (or one naming a different endpoint) leaves a shortfall red. Measured after the fix, the
desktop-only leg `release-evidence/browser-a11y-20260928T211052Z/` reads server 4xx 25 against gate 4xx 25
with `unsettled_at_close: []` and the census exiting 0 with empty stderr, so no exemption was used. Its
artifact carries `gate_issued_events: 261` and `gate_aborted_events: 5`.

The instrumentation also caught a defect this round's own change had introduced. `context.on("page")` is
dispatched during the *next* blocking call, i.e. after the walk has already named that page, so the hook's
fallback label overwrote the Control Plane's own name: in
`release-evidence/browser-a11y-20260928T205945Z/report.json` not a single `desktop-*-admin` label survived,
every one read `…-studio (popup)`. Without the `document` field added the same round I would have read that
as "the admin stage did not run". The hook's label is now a fallback that never wins over a walk's, and the
`-admin` labels came back.

Census self-test 12 → 25 arms (the last two pin the tightened exemption: the named page has to have been waiting on the very endpoint that is short, and a legacy string entry excuses nothing); the browser gate gained real-page fixtures for the second page of an armed
context, the abort axis and the settle axis (each with the polarity that must stay silent). On the resident
side `tests/unit/test_release_record_consistency.py` now pins that an empty unsettled list and a missing
unsettled reading both stay red, and that a report carrying `request_counts` must have the axis written into
its own artifact. No case count is quoted in this section, on purpose: the round added cases four times while it was still writing itself, so every such number was stale before the sentence ended. Each certified run's reading is in its own SUMMARY line `metrics static-verify unit_passed=`, the figures the record quotes are the stamped cells, and what was added after a run is named in that run's following commit messages instead.

## 2026-09-29 the authority moves onto the tree that owns its own figures, and the pointer clause reddens first

Authoritative run `acceptance-20260929T022610Z` (commit `680a0ea`, `fresh_database=1` in the same header, 2026-09-29T02:26:10Z → 2026-09-29T02:49:48Z, 23 rows: 22 PASS and `capacity-gate-500` skipped by switch, host load "5.75 5.52 5.54" on a 4-cpu Docker VM with `disk_free_kb=9204064` beside it), browser record `release-evidence/browser-a11y-20260929T024252Z/report.json`, cross-check `release-evidence/browser-a11y-20260929T024252Z/server-refusals.txt` -- server 4xx 51 against gate 4xx 51, `gate_issued_events: 529`, `gate_aborted_events: 10`, `unsettled_at_close` empty; its own `refusal_census` header prints `server_5xx_events: 0` against `gate_5xx_events: 2`, and those two are the injected stale-panel route, which is a 500 by design and never reaches the api, so the pair is the shape the census documents rather than a third disagreement.

**Nothing product-shaped happened between the previous authority and this one.** Four commits stand between the two trees: a certification of the completeness rule, a pass that named the blocking premise on every open gate box that had stated none, and a step attribution that was corrected and then retracted once its seven mismatches turned out to be seven historical row orders. What this section adds is therefore the run itself: the record now quotes a chain that ran on the tree whose cells own every figure in it, which is the first time those two facts are the same commit's.

**The certification was blocked by its own coverage floor, and that is the designed feedback.** The moment the new `SUMMARY.txt` was staged and the faces re-stamped, `tests/unit/test_release_record_consistency.py` went red twice before a word of this section existed: `test_the_status_file_quotes_each_round_its_own_host_readings` because no line in this file paired the new run stamp with the machine state it started under, and `test_every_authority_line_cites_the_authoritys_own_leg` because the previous authority's own line had been the only place this face named a run and its leg together, so moving the authority silently dropped `FINAL_RELEASE_STATUS.md` out of the clause's per-face roster. A clause that only counted citations in aggregate would have read the drop as "fewer pointers, still zero problems"; the per-face roster is what turns "this face stopped being checked" into a failure. Both went green on the paragraph above, which is why it carries the leg path and the host pair inline rather than in prose further down.

**Ladder.** `unit_passed=481` in the certified run's own step-1 metrics line, and `pytest -q tests/unit` on this tree reads 481 passed, so the run and the tree it is written in agree and no caveat is needed between them -- the state the record was rebuilt to reach, since the last several rounds each had to name the gap. The two provider-latency steps moved up on this run rather than down: 100 jobs at `p50 12.665s / p95 19.12s` and 25 at `p50 6.26s / p95 15.1s`, both `error_rate=0.0`, against 4.18/6.35 and 4.1/5.11 on the run this one replaces. The host pair read "11.60 15.18 13.41" when this chain wrote its `finished_at=` line, i.e. the machine was much busier at the end than the 5.75 it started under, so the series is recorded as a load-coupled reading and not as a regression in the pipeline -- the cells quote the run's numbers regardless of which way they moved, which is the only reason the two sentences can be told apart at all.
