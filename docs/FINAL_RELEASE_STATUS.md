# Resonance v13 Final Release Status

Updated 2026-09-25 after the first end-to-end execution of the cross-container suite in
this delivery environment. Earlier revisions of this file stated that the environment
"does not expose a Docker daemon" and that the Compose E2E had to be run elsewhere; that
was true of the packaging environment but not of this one, and it had stood in place of
evidence for every gate in `RELEASE_CHECKLIST.md`. The gates are now recorded against what
was actually executed.

## Executed on a real Compose stack

Authoritative run: `scripts/acceptance-all.sh` on a fresh database, **15 steps PASS and
1 recorded as skipped**, commit `1d8534e`, 2026-09-25T18:39:55Z → 18:48:49Z, evidence in
`release-evidence/acceptance-20260925T183955Z/` (per-step logs, `SUMMARY.txt`, full
`compose-logs.txt` and `commercial-compose-logs.txt`), with the browser audit's own
machine-readable record at `release-evidence/browser-a11y-20260925T184749Z/report.json`
(stamped with the same commit). The same suite was green one commit earlier as well
(`28deafc`, `release-evidence/acceptance-20260925T181823Z/`), so the pass is reproducible
rather than a single lucky run. Three earlier runs on this day are kept as discovery
records, not as the headline: `acceptance-20260925T161532Z` proved 14 steps while the
refund fix was still uncommitted, `acceptance-20260925T162746Z` is a genuine **FAIL** at
commit `7b187dc` where lease-contention hit an unfunded tenant,
`acceptance-20260925T174204Z` is the **FAIL** in `browser-a11y` that led to the proxy
defect below, and `browser-a11y-20260925T170315Z` is the 27-finding audit that surfaced
the CSP, contrast and overflow defects. Docs that cite this section are committed after the
tested tree; the only later commits touching tooling are followed by a fresh run.

Host: Docker 29.5.2 on a Colima VM with **4 vCPU / 6 GiB**, Compose v5.4.0. The same suite
also runs in CI (`.github/workflows/ci.yml`: `static-and-unit`, `compose-acceptance`,
`commercial-flow`, `browser-a11y`, and `capacity-500`).

> **CI status is deliberately not claimed as evidence.** The branch was pushed, which
> dispatches those jobs, but this environment cannot read their outcome: the repository is
> private (unauthenticated fetch returns 404), the `gh` CLI is not logged in, the GitHub MCP
> connector exposes no workflow-run tool, and the local browser has no GitHub session. Treat
> the CI jobs as dispatched-and-unverified; every number quoted in this file comes from the
> local run above.

- Compose E2E, twice — including once **after** a backup/restore: 11 resident cases over
  live api/worker/web/admin/gateway.
- Provider and payment contract tests: 4 cases.
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
  In the two pipeline runs recorded above: **p50 6.25s / p95 8.31s** and **p50 8.53s /
  p95 13.64s** across 100 finished jobs, 1000 credits settled each time — so the timing
  reading moves with host load and must be quoted per run, not as a property of the code.
- **Real-browser walkthrough + axe audit** (new): Playwright driving axe-core 4.13.0
  (pinned by sha256, fetched at run time, test-only) against the live stack —
  **62 view records, 36 axe scans across desktop 1440 and phone 390, 0 critical and 0
  serious**, 48 moderate (`heading-order`, `landmark-one-main`, `region`) left as follow-up.
  It also asserts what axe cannot see: the shipped CSP must block **zero** inline styles
  authored by the app (measured 0 after the fix, 10 per radar render before), no uncaught
  exceptions, `script-src 'self'` present on both origins, skip-link and keyboard access
  to the nav, and no horizontal overflow at 390px.
- Static verification and unit tests: 66 unit tests (previously recorded here as 23, then 54).

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
before being built. Two such comparisons were made this round, both from material actually
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
