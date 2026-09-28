#!/usr/bin/env python3
"""Real-browser walkthrough plus axe-core audit of Studio and the Control Plane.

Complements the API-level acceptance suite: everything asserted here is what a
browser sees after the JavaScript has run — accessible names, focus, the
JS-rendered tabs/radar/dialogs, and layout at a phone viewport.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import atexit
import collections
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

AXE_VERSION = "4.13.0"
AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"
AXE_TGZ = f"https://registry.npmjs.org/axe-core/-/axe-core-{AXE_VERSION}.tgz"
CACHE = Path(os.environ.get("AXE_CACHE_DIR", ".cache")) / f"axe-core-{AXE_VERSION}.min.js"

WEB_URL = os.environ.get("WEB_URL", "http://127.0.0.1:4173")
ADMIN_URL = os.environ.get("ADMIN_URL", "http://127.0.0.1:4174")
REPO_ROOT = Path(__file__).resolve().parents[1]
# (label, base url, the repository file that image serves verbatim)
ORIGINS = (("studio", WEB_URL, "services/web/index.html"), ("control-plane", ADMIN_URL, "services/admin/index.html"))
EMAIL = os.environ.get("E2E_EMAIL", "owner@example.local")
PASSWORD = os.environ.get("E2E_PASSWORD", "demo-owner")
VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}
# moderate joined the blocking set on 2026-09-27, when the last 64 of them were closed (the sign-in
# screen had no landmark, and panel headings jumped h1 -> h3). Leaving the set at critical/serious
# is what let those sit for four rounds while the gate read green, so the set is now the widest axe
# impact this product's pages can be held to; `minor` stays tolerated because no rule in this
# axe version reports minor on these pages, and a criterion nobody can trip is not a criterion.
BLOCKING_IMPACTS = ("critical", "serious", "moderate")
BLOCKING_CONSOLE = re.compile(r"uncaught|refused to execute|would have been blocked|failed to fetch", re.I)
CSP_BLOCK = re.compile(r"violates the following content security policy|refused to apply inline style", re.I)

# axe reads computed colours at the instant it runs, so a button caught mid
# transition yields an interpolated ratio no user ever settles on. Two identical
# paint samples a full transition-length apart (the app transitions background in
# .18s) is what "as deployed" means for a colour check.
PAINT_SIG_JS = """() => Array.from(document.querySelectorAll('body *')).slice(0, 400)
  .map(e => { const c = getComputedStyle(e); return c.color + '/' + c.backgroundColor; }).join(';')"""


CLIP_JS = """() => {
  const reachable = (e) => {
    for (let p = e; p; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === 'auto' || o === 'scroll') return true;
    }
    return false;
  };
  const path = (e) => (e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
    + Array.from(e.classList).slice(0, 2).map(c => '.' + c).join('')
    + ' ' + e.scrollWidth + '>' + e.clientWidth);
  const visible = (e) => {
    if (getComputedStyle(e).display === 'none' || getComputedStyle(e).visibility === 'hidden') return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const out = {clipped: 0, paths: [], scrollers: 0, fields: 0};
  for (const e of document.querySelectorAll('body *')) {
    if (e.scrollWidth <= e.clientWidth + 1 || e.clientWidth === 0) continue;
    if (reachable(e)) { out.scrollers += 1; continue; }
    if (!visible(e)) continue;
    // The value of a form control is not content the page withholds: a 383px e-mail inside a 314px
    // input is the platform working as designed -- the person moves the caret and sees the rest.
    if (e instanceof HTMLInputElement || e instanceof HTMLTextAreaElement
        || e instanceof HTMLSelectElement || e.isContentEditable) { out.fields += 1; continue; }
    out.clipped += 1;
    if (out.paths.length < 4) out.paths.push(path(e));
  }
  return out;
}"""


def _utc_ms() -> str:
    """Wall clock with milliseconds, the only resolution that can attribute a missed refusal.

    The cross-check compares the gate's per-event log against `docker compose logs api`. Both sides wrote
    second-granular stamps, so when the two observers disagreed by one event -- as they did on
    `acceptance-20260928T163801Z` and again on `acceptance-20260928T194108Z` -- the surplus landed in a
    second that also held several events the gate *did* see, and "a response arrived after the page closed"
    was indistinguishable from "the hook never fired". Milliseconds make the two orders comparable; the
    census prints them.
    """
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % int((now % 1) * 1000)


def unsettled_entries(entries: list[dict]) -> list[dict]:
    """Order and de-duplicate the teardown notes without asking a dict to be hashable.

    The crash this replaces was live: the report line was `sorted(set(auditor.unsettled))` after the notes
    became dicts, so a run with ANY unsettled page raised TypeError while writing its own report -- which
    means the reading the refusal census needs in order to excuse a shortfall could only ever have been
    the empty list, and the exemption was unreachable in practice rather than by design.
    """
    out: dict[tuple, dict] = {}
    for entry in entries:
        key = (str(entry.get("label", "")), str(entry.get("error", "")),
               tuple(sorted(entry.get("outstanding") or [])))
        out[key] = entry
    return [out[key] for key in sorted(out)]


def url_path(url: str) -> str:
    """The path the api itself logs: no origin, no query.

    Both observers in the cross-check must key on the same string or the comparison is two censuses of
    different things -- the api logs `GET /api/projects?limit=20`, the release record quotes one endpoint.
    """
    return re.sub(r"^[a-z]+://[^/]+", "", url).split("?")[0]


# Requests this process makes outside any page (scripts/browser_a11y.py:api_post). They land in the same
# api access log the census reads, so an uncounted host call would read as "the auditor missed traffic" --
# the false red the request axis exists to avoid.
HOST_ISSUED: collections.Counter = collections.Counter()
HOST_TIMELINE: list[dict] = []


def wait_painted(page, gap_ms: int = 250, tries: int = 12) -> bool:
    previous = None
    for _ in range(tries):
        page.wait_for_timeout(gap_ms)
        current = page.evaluate(PAINT_SIG_JS)
        if previous is not None and current == previous:
            return True
        previous = current
    return False


def axe_source() -> str:
    if CACHE.exists():
        cached = CACHE.read_bytes()
        if hashlib.sha256(cached).hexdigest() == AXE_SHA256:
            return cached.decode()
    blob = urllib.request.urlopen(AXE_TGZ, timeout=120).read()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        member = tar.extractfile("package/axe.min.js").read()
    digest = hashlib.sha256(member).hexdigest()
    if digest != AXE_SHA256:
        raise SystemExit(f"axe-core {AXE_VERSION} checksum mismatch: {digest} != {AXE_SHA256}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(member)
    return member.decode()


def gate_failures(scans: list[dict]) -> list[str]:
    """Blocking findings only: a view whose audit saw nothing is not a pass."""
    failures = []
    for scan in scans:
        if not scan.get("axe"):
            continue
        if not scan.get("audited"):
            failures.append(f"{scan['label']}: axe reported nothing (scan did not run)")
            continue
        if scan.get("settle") != "stable":
            failures.append(f"{scan['label']}: paint never settled, so colour readings would be interpolated")
        for violation in scan["violations"]:
            if violation["impact"] in BLOCKING_IMPACTS:
                failures.append(
                    f"{scan['label']}: {violation['id']} ({violation['impact']}) on "
                    f"{violation['node_count']} node(s); {violation.get('why', '')}"
                )
    return failures


def mobile_fit_failures(scans: list[dict], limit: int = 1, require: bool = True) -> list[str]:
    failures = []
    checked = 0
    for scan in scans:
        if scan.get("viewport") != "mobile":
            continue
        checked += 1
        if scan["scroll_width"] > scan["client_width"] + limit:
            failures.append(
                f"{scan['label']}: horizontal overflow {scan['scroll_width']}px > {scan['client_width']}px"
            )
    if not checked and require:
        failures.append("no mobile viewport was measured")
    return failures


def clip_failures(scans: list[dict], require: bool = True) -> list[str]:
    """A box that holds more than it shows, with nothing behind it to scroll to the rest.

    The fit check above only says the document does not scroll sideways, and a layout can buy that by
    refusing to grow: `minmax(0,1fr)` keeps the page at 390px while cutting the tail off a row, which
    is a quieter lie than an overflow bar. Both viewports are judged, because a fixed track clips at
    1440 just as well as at 390.
    """
    failures, measured = [], 0
    for scan in scans:
        if scan.get("clipped") is None:
            continue
        measured += 1
        if scan["clipped"]:
            failures.append(
                f"{scan['label']} [{scan['viewport']}]: {scan['clipped']} element(s) hold more than they "
                f"show with no scroll container in front of them: {'; '.join(scan['clipped_paths'])}")
    if not measured and require:
        failures.append("no scan measured whether content is clipped inside its own box")
    return failures


def csp_failures(headers: dict[str, str], origins: list[str]) -> list[str]:
    failures = []
    if not origins:
        return ["no origins were checked for a content security policy"]
    for origin in origins:
        value = headers.get(origin)
        if value is None:
            failures.append(f"{origin}: no 200 document response was observed")
            continue
        directive = next((d.strip() for d in value.split(";") if d.strip().startswith("script-src")), None)
        if not directive:
            failures.append(f"{origin}: policy has no script-src directive ({value})")
        elif "'self'" not in directive.split() or "'unsafe-inline'" in directive.split():
            failures.append(f"{origin}: script-src is not restricted to 'self' ({directive})")
    return failures


def hidden_failures(scans: list[dict], require: bool = True) -> list[str]:
    """An element the app marks `hidden` must not render.

    `[hidden] { display: none }` lives in the UA stylesheet, so one author rule such as
    `label { display: grid }` outranks it and the attribute stops hiding anything -- which is how the
    erasure panel's verification-code field rendered for a probe that has no second factor. The sweep
    is page-wide on purpose: the trap belongs to the stylesheet, not to the panel that tripped it.

    `require` also refuses a run that never saw an element marked hidden at all, because a sweep over
    an empty denominator reads exactly like a clean one.
    """
    offenders = sorted({f"{scan['label']}: {name}" for scan in scans
                        for name in scan.get("hidden_but_rendered") or []})
    failures = []
    if offenders:
        failures.append(f"{len(offenders)} element(s) marked hidden still render: {', '.join(offenders[:6])}")
    if require and sum(int(scan.get("hidden_marked") or 0) for scan in scans) == 0:
        failures.append("no scanned page held a single element marked hidden, so the sweep proved nothing")
    return failures


class Auditor:
    axe_path = "/__axe/axe.min.js"

    def __init__(self, axe: str, out_dir: Path, run_axe: bool = True, relax_csp: bool = False):
        self.axe = axe
        self.out_dir = out_dir
        self.run_axe = run_axe
        self.relax_csp = relax_csp
        self.scans: list[dict] = []
        self.errors: list[str] = []
        self.refusals: list[str] = []
        # `refusals` answers "which refusals were seen"; it cannot answer "when", and a disagreement with
        # the api access log needs the when: 27 server-side 401s against 26 gate-side ones is a different
        # defect depending on whether the extra falls at the run's boundary or in its middle.
        self.refusal_timeline: list[dict] = []
        # The refusal axis can only count what a page was handed. When the two observers disagree by one
        # event, the question is "did the browser send it and never get the answer back" or "did a session
        # the auditor never armed talk to the api" -- and only a counter taken *before* the request leaves
        # can tell them apart. So this one counts every request this process put on the wire.
        self.issued: collections.Counter = collections.Counter()
        # ...and this records the requests that got no response at all, which is the face of a refusal the
        # response hook structurally cannot see (the page was reloaded or closed while it was in flight).
        self.aborted: list[dict] = []
        # Pages that had not gone network-idle when the walk closed them -- the one condition under which a
        # refusal can be logged by the api and never reach any observer, so the census reads this list
        # before it reads a shortfall on the refusal axis as a blind spot.
        self.unsettled: list[dict] = []
        # Per observed page, the requests it had put on the wire and not yet been handed an answer for.
        # The census reads this before it lets an unsettled page excuse a shortfall: "a page was closed
        # while it was still waiting" only explains the missing 401 if the endpoint it waited on *is* the
        # one that is short -- otherwise the note is a tolerance with a label on it.
        self._outstanding: dict[int, collections.Counter] = {}
        # Pages and contexts are keyed by id() with the object kept as the value: holding the reference is
        # what makes the address a stable key, and dropping it would let a recycled address inherit another
        # page's "already armed" mark -- a self-inflicted version of the blindness this fixes.
        self._armed: dict[int, object] = {}
        self._armed_contexts: dict[int, object] = {}
        self._observed: dict[int, object] = {}
        self._labels: dict[int, str] = {}
        self._context_labels: dict[int, str] = {}
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.crashes: list[str] = []
        self.csp_blocks: list[str] = []
        self.security_headers: dict[str, str] = {}

    def arm(self, page):
        self._arm_page(page)
        context = page.context
        if id(context) in self._armed_contexts:
            return
        self._armed_contexts[id(context)] = context

        def on_page(later):
            self._arm_page(later)
            # Watched, not merely routed: a page this context opens later still reaches the api, and an
            # unobserved one would read as the gate missing traffic.
            #
            # Deferred dispatch is the trap here: Playwright delivers the "page" event while the next
            # blocking call runs, which in the main loop is *after* that walk has already labelled its own
            # page. So this label is a fallback -- overwriting it would rename the Control Plane's
            # refusals after the studio page that shares its context (measured on
            # `browser-a11y-20260928T205945Z`, where every `-admin` refusal came out labelled
            # `…-studio (popup)`).
            _ctx, origin = self._context_labels.get(id(context), (None, "unlabelled"))
            self.attach_console(later, f"{origin} (popup)", fallback=True)

        context.on("page", on_page)

    def _arm_page(self, page):
        if id(page) in self._armed:
            return
        self._armed[id(page)] = page
        page.route(re.compile(".*"), self._route)

    def settle(self, page, label: str):
        """Give the page's own outstanding requests an answer before it is closed.

        Without this the gate's refusal axis is short by however many requests were in flight at teardown:
        the api logs the answer, the browser never hands it to a page that is going away, and `requestfailed`
        says nothing either -- which is exactly the shape the census could not attribute until the request
        axis proved the auditor had issued every one of those lines. Waiting for the network to go idle
        closes the blind window instead of tolerating it, and a page that never goes idle is recorded rather
        than silently excused.
        """
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as exc:
            counter = self._outstanding.get(id(page))
            self.unsettled.append({
                "label": label,
                "error": type(exc).__name__,
                "outstanding": sorted(counter.elements()) if counter is not None else ["NO-READING"],
            })

    def settle_context(self, context, label: str):
        """Settle every page a context still holds, so no in-flight answer is lost to teardown."""
        for page in list(context.pages):
            self.settle(page, label)

    def _route(self, route):
        request = route.request
        if request.url.endswith(self.axe_path):
            route.fulfill(status=200, content_type="application/javascript", body=self.axe)
            return
        self.issued[f"{request.method} {url_path(request.url)}"] += 1
        if self.relax_csp and request.resource_type == "document":
            # axe injects its own styles; under the shipped style-src 'self' it is
            # blinded and reports bogus contrast failures. Strip the policy only for
            # the audit pass — the as-deployed pass keeps it and asserts on it.
            fetched = route.fetch()
            headers = {k: v for k, v in fetched.headers.items() if k.lower() != "content-security-policy"}
            route.fulfill(status=fetched.status, body=fetched.body(), headers=headers)
            return
        route.continue_()

    def account_host_calls(self):
        """Fold this process's out-of-page requests into the same axes the pages feed.

        The api answers them like any other request, and the census reads that answer: leaving them out
        would let the auditor's own provisioning call read as "a session the gate never armed".
        """
        self.issued.update(HOST_ISSUED)
        for entry in HOST_TIMELINE:
            self.refusal_timeline.append(entry)
            self.refusals.append(f"{entry['label']}: {entry['event']}")

    def record_headers(self, origin: str, response):
        if self.relax_csp or not response or response.status != 200:
            return
        self.security_headers[origin] = response.headers.get("content-security-policy", "<absent>")

    def attach_console(self, page, label: str, fallback: bool = False):
        context = page.context
        self._context_labels.setdefault(id(context), (context, label))
        if not fallback or id(page) not in self._labels:
            # The walk's own label is authoritative; the context hook's is only a fallback, because Playwright
            # delivers the "page" event during the next blocking call -- often after the walk already named
            # the page, and overwriting it there would relabel that session's refusals.
            self._labels[id(page)] = label
        if id(page) in self._observed:
            # The context hook may have reached this page first; re-registering the listeners would instead
            # record every refusal this page sees twice.
            return
        self._observed[id(page)] = page

        def named() -> str:
            return self._labels.get(id(page), label)

        def on_console(message):
            if message.type != "error":
                return
            entry = f"{named()}: {message.text}"
            self.errors.append(entry)
            if CSP_BLOCK.search(message.text):
                self.csp_blocks.append(entry)
            if BLOCKING_CONSOLE.search(message.text):
                self.crashes.append(entry)

        outstanding = collections.Counter()
        self._outstanding[id(page)] = outstanding

        def on_request(request):
            outstanding[f"{request.method} {url_path(request.url)}"] += 1

        def on_release(request):
            key = f"{request.method} {url_path(request.url)}"
            if outstanding[key]:
                outstanding[key] -= 1

        page.on("console", on_console)
        page.on("request", on_request)
        # The request side of the same story. `console_errors` is a deduplicated set of "<label>: <text>",
        # and the network layer's text carries only the status, so two different refusals under one label
        # collapse into a single line -- which is how a hand-written sentence came to claim six refusals
        # for what the server answered as eighteen. Every 4xx/5xx the page actually sees is recorded here
        # with its method and path, and the raw event counts join the report alongside the line counts.
        def on_response(response):
            if response.status < 400:
                return
            path = url_path(response.url)
            event = f"{named()}: {response.request.method} {path} -> {response.status}"
            self.refusals.append(event)
            self.refusal_timeline.append({
                "ts": _utc_ms(),
                "label": named(),
                "document": page.url,
                "event": f"{response.request.method} {path} -> {response.status}",
            })

        def on_request_failed(request):
            key = f"{request.method} {url_path(request.url)}"
            outstanding[key] = max(0, outstanding[key] - 1)
            if request.method in ("GET", "POST", "PUT", "PATCH", "DELETE") and url_path(request.url).startswith("/api/"):
                self.aborted.append({
                    "ts": _utc_ms(),
                    "label": named(),
                    "document": page.url,
                    "event": f"{request.method} {url_path(request.url)} -> no response ({request.failure})",
                })

        page.on("response", on_response)
        page.on("requestfinished", on_release)
        page.on("requestfailed", on_request_failed)
        page.on("pageerror", lambda e: self.crashes.append(f"{named()}: uncaught exception: {e}"))

    def script_url(self, page) -> str:
        origin = page.evaluate("() => location.origin")
        if isinstance(origin, str) and origin.startswith("http"):
            return origin + self.axe_path
        return f"http://axe.local{self.axe_path}"

    def scan(self, page, label: str, viewport: str, require: str | None = None):
        if not self.run_axe:
            label = f"{label} (as-deployed)"
        if require:
            page.wait_for_selector(require, timeout=20000)
        settle = "stable" if wait_painted(page) else "timeout"
        violations: list[dict] = []
        audited = False
        if self.run_axe:
            if not page.evaluate("() => !!window.axe"):
                page.add_script_tag(url=self.script_url(page))
            raw = page.evaluate("() => axe.run(document, {resultTypes: ['violations']})")
            audited = len(raw.get("passes") or []) + len(raw["violations"]) > 0
            violations = [
                {
                    "id": v["id"],
                    "impact": v.get("impact") or "incomplete",
                    "help": v["help"],
                    "node_count": len(v["nodes"]),
                    "why": (v["nodes"][0].get("failureSummary") or "").replace("\n", " ")[:300] if v["nodes"] else "",
                    "targets": [n["target"][0] if isinstance(n["target"], list) else n["target"] for n in v["nodes"][:3]],
                }
                for v in raw["violations"]
            ]
        metrics = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        # A page that fits is not the same claim as a page that shows what it holds: the fix for
        # horizontal overflow can just as well be a track that refuses to grow, which hides the tail
        # of a row behind `minmax(0,...)`. So measure the inside of the boxes too. Deliberate scroll
        # containers are counted separately and never judged, because a wide table behind
        # `overflow-x:auto` is the app choosing to let a person reach the rest; only content that is
        # neither visible nor reachable counts as clipped.
        clip_state = page.evaluate(CLIP_JS)
        hidden_state = page.evaluate(
            """() => {
                const marked = [...document.querySelectorAll('[hidden]')];
                return {marked: marked.length,
                        rendered: marked.filter(el => getComputedStyle(el).display !== 'none')
                                        .map(el => el.id ? '#' + el.id : el.tagName.toLowerCase())};
            }"""
        )
        shot = self.out_dir / f"{viewport}-{label.replace('/', '-').replace(' ', '_')}.png"
        page.screenshot(path=str(shot), full_page=False)
        self.scans.append(
            {
                "label": label,
                "viewport": viewport,
                "url": page.url,
                "axe_version": AXE_VERSION,
                "axe": self.run_axe,
                "audited": audited,
                "settle": settle,
                "violations": violations,
                "scroll_width": metrics["scroll"],
                "client_width": metrics["client"],
                "clipped": clip_state["clipped"],
                "clipped_paths": clip_state["paths"],
                "scroll_containers": clip_state["scrollers"],
                "clipped_fields": clip_state["fields"],
                "hidden_marked": hidden_state["marked"],
                "hidden_but_rendered": sorted(set(hidden_state["rendered"])),
                "screenshot": str(shot),
            }
        )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for scan in self.scans:
            for violation in scan["violations"]:
                counts[violation["impact"]] = counts.get(violation["impact"], 0) + 1
        return counts


RATE_LIMITED = re.compile(r"too many login attempts|429", re.I)


def login_error(page) -> str:
    return page.evaluate(
        "() => { const e = document.querySelector('#loginError') || document.querySelector('#error');"
        " return e ? e.textContent.trim() : ''; }"
    )


def login(page, base: str, auditor: Auditor, viewport: str, tries: int = 5, email: str = EMAIL, password: str = PASSWORD):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    response = page.goto(base, wait_until="networkidle")
    auditor.record_headers(base, response)
    auditor.scan(page, "login", viewport, require="#loginForm")
    # The bypass link has to land on something rendered in whichever state the page is in. On the sign-in
    # screen its href used to be #main, which lives inside the hidden #app, so Enter moved focus nowhere;
    # keyboard_checks() holds the signed-in half to the same bar by following the focus itself.
    skip = page.evaluate(
        "() => { const a = document.querySelector('.skip-link');"
        " const href = a ? (a.getAttribute('href') || '#__none__') : '#__none__';"
        " const t = document.querySelector(href);"
        " return {href: a ? a.getAttribute('href') : null, rendered: !!t && !!t.offsetParent}; }")
    if skip.get("href") != "#login" or not skip.get("rendered"):
        raise SystemExit(f"{base}: the sign-in screen's skip link does not bypass to something rendered: {skip}")
    detail = ""
    for attempt in range(tries):
        page.fill("#email", email)
        page.fill("#password", password)
        page.press("#password", "Enter")
        try:
            page.wait_for_selector("#app:not([hidden])", timeout=10000)
            page.wait_for_function(
                "() => { const pill = document.querySelector('#creditPill'); return !pill || /\\d/.test(pill.textContent); }",
                timeout=20000)
            return
        except PlaywrightTimeout:
            detail = login_error(page)
            # The counter is keyed per account (login:sha256(email)) and counts ONLY failed
            # credentials, in a fixed 60-second window: a refusal reads it without extending it,
            # so 61s is guaranteed to clear it. Successful logins no longer spend the budget,
            # which is why the logins this suite performs are no longer what fills the window.
            page.wait_for_timeout(61000 if RATE_LIMITED.search(detail) else 3000)
    raise SystemExit(f"login never succeeded after {tries} attempts: {detail or 'no error text'}")


def press_until(page, button, expect: str, what: str, tries: int = 12):
    """Click, then wait for the state the click is meant to produce, retrying the click.

    This is for controls whose *result* depends on data still arriving (a dialog that needs a fetch, a
    panel that fills itself on first open). It is not for the boot window any more: navigation handlers
    used to be bound only after the first data load, so a click could land on an inert button, and
    retrying hid that. walk_boot_click() now measures the boot window on purpose, with one click.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for _ in range(tries):
        button.click()
        try:
            page.wait_for_selector(expect, timeout=2000)
            return
        except PlaywrightTimeout:
            page.wait_for_timeout(400)
    raise SystemExit(f"clicking {what} never produced {expect}")


def goto_view(page, name: str, marker: str):
    press_until(page, page.get_by_role("button", name=f"{name}视图"), f"#{marker}.active", f"{name} view")


def goto_tab(page, name: str, pane: str):
    press_until(page, page.get_by_role("tab", name=name), f"#{pane}.active", f"{name} tab")


BOOT_HOLD = 3.0


def walk_boot_click(browser, size, auditor: Auditor, viewport: str) -> list[str]:
    """One top-bar click, issued while boot is still loading, must switch the view.

    The defect this measures (RELEASE_CHECKLIST 待属主定值 2): `#app` became visible two awaited fetches
    before bindNavigation() put handlers on the nav buttons, so the first click a person makes on a slow
    connection did nothing at all. press_until() clicked again until it worked, which made the symptom
    unobservable -- a workaround cannot also be the evidence.

    /api/bootstrap is held for BOOT_HOLD seconds so the window is produced rather than raced for, and both
    premises are read back before the single click is judged: the held request really did reach this handler,
    and `#creditPill` -- which init writes only after that fetch returns -- still carried no number when the
    click was issued. Either premise failing returns a finding instead of a silent pass, because a check
    that cannot tell "fixed" from "never exercised" is the shape this repository keeps having to relearn.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    held: list[str] = []
    context = browser.new_context(viewport=size)
    page = context.new_page()
    auditor.arm(page)

    def slow(route):
        held.append(route.request.url)
        time.sleep(BOOT_HOLD)
        route.continue_()

    auditor.attach_console(page, f"{viewport}-boot-click")
    try:
        login(page, WEB_URL, auditor, viewport)
        # Registered after arm(): with two matching routes the last one registered is consulted first,
        # which is what walk_stale_panels measured the hard way.
        page.route("**/api/bootstrap", slow)
        page.goto(WEB_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#app:not([hidden])", timeout=20000)
        pill = (page.locator("#creditPill").text_content() or "").strip()
        if re.search(r"\d", pill):
            return [f"boot had already finished when the click was issued (creditPill={pill!r}), "
                    "so the window this check exists for was never exercised"]
        if not held:
            return ["the held /api/bootstrap never reached the route handler, so the click was not "
                    "issued during boot"]
        page.get_by_role("button", name="资产视图").click()
        try:
            page.wait_for_selector("#view-assets.active", timeout=1500)
        except PlaywrightTimeout:
            return [f"a single nav click at {pill!r} (boot still loading) did not switch the view, "
                    "so the handlers were not bound when #app became visible"]
        if page.locator("#view-creation.active").count():
            return ["the boot-window click switched to 资产 without leaving 创作"]
        from playwright.sync_api import TimeoutError as _Timeout
        try:
            # The held fetch has to come back and be rendered: a click that switched the view but left
            # the page starved of its data would be a different defect, not a pass. Judged by waiting for
            # the pill to carry a number, because at this instant the hold is still running by design.
            page.wait_for_function(
                "() => { const p = document.querySelector('#creditPill'); return p && /\\d/.test(p.textContent); }",
                timeout=int((BOOT_HOLD + 12) * 1000))
        except _Timeout:
            return ["the held /api/bootstrap came back but the app never painted its numbers, "
                    "so the view the click switched to was left without data"]
        return []
    finally:
        auditor.settle_context(context, f"{viewport}-boot-click")
        context.close()


def walk_studio(page, auditor: Auditor, viewport: str):
    if page.locator("#creationEmpty").is_visible():
        press_until(page, page.get_by_role("button", name="创建第一首歌"), "#dialog[open]", "create first song")
        auditor.scan(page, "studio-create-dialog", viewport)
        page.get_by_label("歌曲名称").fill(f"浏览器验收 {time.strftime('%m%dT%H%M%SZ', time.gmtime())}")
        press_until(page, page.get_by_role("button", name="创建", exact=True), "#studio:not([hidden])", "create")
    page.wait_for_selector("#studio:not([hidden])", timeout=20000)
    if page.locator("#projects > *").count() == 0:
        raise SystemExit("the project rail stayed empty, so the studio was never really exercised")
    auditor.scan(page, "studio-project", viewport)

    page.fill("#chatMessage", "让副歌更克制，减少鼓组密度。")
    press_until(page, page.get_by_role("button", name="运行确定性评估"), "#radarGrid .radar-chart", "quality run")
    if page.locator("#radarGrid .radar-point").count() == 0:
        raise SystemExit("quality engine rendered no radar points, so the a11y surface was not exercised")
    auditor.scan(page, "studio-quality", viewport, require="#qualityGrade")

    press_until(page, page.get_by_role("button", name="生成报价"), "#submitJob:not([disabled])", "quote")
    auditor.scan(page, "studio-quote", viewport)

    press_until(page, page.get_by_role("button", name="版本历史"), "#dialog[open]", "version history")
    auditor.scan(page, "studio-dialog", viewport)
    page.get_by_role("button", name="关闭对话框").click()
    page.wait_for_function("() => !document.querySelector('#dialog').open", timeout=15000)


def walk_views(page, auditor: Auditor, viewport: str, nav: str = "mainNav"):
    views = {"mainNav": (("资产", "view-assets"), ("市场", "view-market"), ("数据与账户", "view-account")),
             "adminNav": (("任务", "view-jobs"), ("财务", "view-billing"),
                          ("信任与支持", "view-trust"), ("发布证据", "view-release"))}[nav]
    for view, marker in views:
        goto_view(page, view, marker)
        if view == "市场":
            auditor.scan(page, "market-credits", viewport)
            for tab, pane in (("音乐许可", "market-offers"), ("品牌任务", "market-briefs"), ("订单与交付", "market-orders")):
                goto_tab(page, tab, pane)
                auditor.scan(page, f"market-{tab}", viewport)
            goto_tab(page, "额度与订阅", "market-credits")
        elif view == "数据与账户":
            auditor.scan(page, "account", viewport, require="#ledgerBalances *")
        else:
            auditor.scan(page, f"{nav}-{view}", viewport)


def keyboard_checks(page, auditor: Auditor, viewport: str) -> list[str]:
    failures = []
    page.goto(page.url, wait_until="networkidle")
    page.wait_for_selector("#app:not([hidden])", timeout=20000)
    page.focus(".skip-link")
    page.keyboard.press("Enter")
    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    if focused != "main":
        failures.append(f"skip link did not move focus to #main (focus landed on '{focused}')")
    page.evaluate("() => document.activeElement.blur()")
    reached_nav = False
    for _ in range(20):
        page.keyboard.press("Tab")
        if page.evaluate("() => !!document.activeElement.closest('#mainNav')"):
            reached_nav = True
            break
    if not reached_nav:
        failures.append("Tab never reached a main-nav button within 20 presses")
    return failures


def hex_luminance(color: str) -> float:
    """WCAG 2.1 relative luminance of a hex string, in #rgb or #rrggbb form."""
    digits = color.strip().lstrip("#").lower()
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    if len(digits) != 6:
        raise AssertionError(f"not a hex colour: {color!r}")
    channels = [int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = max(hex_luminance(foreground), hex_luminance(background)), \
        min(hex_luminance(foreground), hex_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def css_token(css: str, name: str) -> str:
    found = re.search(r"--" + re.escape(name) + r"\s*:\s*(#[0-9a-fA-F]{3,6})\b", css)
    if not found:
        raise AssertionError(f"the stylesheet declares no --{name} hex token")
    return found.group(1).lower()


def danger_pair(css: str) -> tuple[str, str]:
    """The (foreground, background) the filled danger control actually paints."""
    block = re.search(r"button\.danger\s*\{([^}]*)\}", css)
    if not block:
        raise AssertionError("no button.danger rule to read")
    body = block.group(1)

    def resolve(value: str) -> str:
        variable = re.fullmatch(r"var\(\s*(--[a-z-]+)\s*\)", value.strip())
        return css_token(css, variable.group(1)[2:]) if variable else value.strip().lower()

    colour = re.search(r"(?:^|;)\s*color\s*:\s*([^;]+);", body)
    background = re.search(r"background\s*:\s*([^;]+);", body)
    if not colour or not background:
        raise AssertionError("button.danger does not declare both a color and a background")
    return resolve(colour.group(1)), resolve(background.group(1))


def origin_verdict(expected_sha: str, body: bytes) -> bool:
    return hashlib.sha256(body).hexdigest() == expected_sha


def preflight_origins(page) -> None:
    """Refuse to audit an origin that is not the app under test.

    On 2026-09-26 another project on this host started `vite preview` on 4173 in the middle of a
    release run. `localhost` resolves to ::1 first, so the gate drove that other app and went red on
    a missing #loginForm. The red was honest; a *green* under the same collision would have audited
    somebody else's site and reported it as this release. Each image COPYs its index.html verbatim,
    so the first paint is compared byte for byte against the repository file -- which also catches an
    image built from a different tree than the one being certified.

    The fetch goes through the browser's own request context rather than urllib: the first version of
    this check used urllib and passed while Chromium was still reaching the intruder, because the two
    take different address families for `localhost`.
    """
    for name, url, relative in ORIGINS:
        source = REPO_ROOT / relative
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        try:
            response = page.request.get(url.rstrip("/") + "/", timeout=20000)
            body = response.body()
        except Exception as exc:  # noqa: BLE001 - the message has to name the origin it failed on
            raise SystemExit(f"origin check: {name} at {url} could not be identified: {exc}")
        if response.status != 200:
            raise SystemExit(f"origin check: {name} at {url} answered {response.status}, not 200")
        if not origin_verdict(expected, body):
            title = re.search(rb"<title>(.*?)</title>", body, re.S)
            found = title.group(1).decode("utf-8", "replace")[:60] if title else "<no title>"
            raise SystemExit(
                f"origin check: {url} is not the {name} under test -- "
                f"served sha256={hashlib.sha256(body).hexdigest()[:12]} expected {expected[:12]} "
                f"(title={found!r}). Another process has taken this host port, or the image was "
                f"built from a different tree than {relative}."
            )
        print(f"origin check: {name} at {url} is byte-identical to {relative}, as the browser resolves it")


def self_test(auditor: Auditor) -> int:
    """Every arm below must be able to fail: a scanner that cannot flag a planted
    violation, or a gate that cannot be satisfied, would otherwise green quietly."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS["desktop"])
        auditor.attach_console(page, "self-test")
        page.set_content(
            "<!doctype html><html lang='zh-CN'><head><title>probe</title></head><body>"
            "<img src='data:image/png;base64,iVBORw0KGgo='/><div id='ok' tabindex='0'>ok</div></body></html>"
        )
        auditor.arm(page)
        auditor.scan(page, "injected-probe", "desktop")

        guard = browser.new_page()
        guard.set_content(
            "<!doctype html><html lang='zh-CN'><head>"
            "<meta http-equiv='Content-Security-Policy' content=\"script-src 'self'\">"
            "<title>csp</title></head><body>x</body></html>"
        )
        try:
            guard.add_script_tag(content="window.__inline = 1")
            inline_blocked = guard.evaluate("() => window.__inline === undefined")
        except Exception:
            inline_blocked = True

        anim = browser.new_page(viewport=VIEWPORTS["desktop"])
        anim.set_content(
            "<!doctype html><html lang='zh-CN'><head><title>anim</title><style>"
            "#b{color:#000;transition:color 3s linear}#b.go{color:#f00}</style></head>"
            "<body><div id='b'>x</div></body></html>"
        )
        anim.evaluate("() => document.querySelector('#b').classList.add('go')")
        unstable_during_transition = not wait_painted(anim, tries=3)
        settled_after = wait_painted(anim, tries=24)

        # The clip probe, both polarities on real layout rather than on a hand-written dict: content
        # hidden by a box that refuses to grow must be named, and the same content behind a scroll bar,
        # inside a form control, or given room to wrap must not be -- otherwise the check would read as
        # green simply because it excludes everything it can see.
        clip = browser.new_page(viewport=VIEWPORTS["mobile"])
        token = "A" * 200
        clip.set_content(
            "<!doctype html><html lang='zh-CN'><head><title>clip</title><style>"
            ".box{width:120px;height:20px;font:12px/20px monospace;white-space:nowrap}"
            "#cut{overflow:hidden}#reach{overflow-x:auto}"
            "#wrap{white-space:normal;overflow-wrap:anywhere;height:auto}</style></head><body>"
            f"<div id='cut' class='box'>{token}</div>"
            f"<div id='reach' class='box'>{token}</div>"
            f"<div id='wrap' class='box'>{token}</div>"
            f"<input id='field' class='box' value='{token}'></body></html>"
        )
        clip_probe = clip.evaluate(CLIP_JS)

        # The observer's own coverage and its two axes, on a real second page in an armed context. The
        # refusal axis records only answers a page was handed; the request axis counts what the auditor put
        # on the wire; the abort axis names the requests that got no answer. The census needs all three: a
        # server line the gate did not record is only attributable once the gate can say whether it issued
        # the request at all. Whether `w3m-abort-probe.invalid` (reserved by RFC 6761) answers with a proxy
        # 502 or with nothing at all depends on this host's network settings, so the arms below ask only
        # that the request be accounted for exactly once, on whichever axis the outcome belongs to.
        armed_context = browser.new_context(viewport=VIEWPORTS["desktop"])
        walk_page = armed_context.new_page()
        auditor.arm(walk_page)
        auditor.attach_console(walk_page, "self-test-walk")
        popup = armed_context.new_page()
        popup_outcome = []
        for target in ("popup-a", "popup-b"):
            try:
                popup.goto(f"http://w3m-abort-probe.invalid/api/{target}", timeout=8000)
                popup_outcome.append(f"{target}: answered")
            except Exception as exc:
                popup_outcome.append(f"{target}: {str(exc)[:60]}")
            if target == "popup-a":
                # Reached by the context hook first: the walk's own label has to replace the hook's without
                # giving the page a second set of listeners, or every refusal it sees counts twice.
                auditor.attach_console(popup, "self-test-popup")

        # A request with no answer at all, produced without depending on the network: Playwright consults
        # the most recently registered route first -- the same ordering fact walk_stale_panels pins -- so
        # this abort is answered by the fixture's own handler and never reaches the auditor's catch-all.
        # That is the point: it proves the abort axis fires, and that it is a different witness than the
        # request axis.
        popup.route("**/api/aborted-x", lambda route: route.abort())
        try:
            popup.goto("http://w3m-abort-probe.invalid/api/aborted-x", timeout=8000)
        except Exception:
            pass
        popup.close()
        walk_page.close()
        armed_context.close()

        def accounted(target: str) -> list[str]:
            hits = [e["label"] for e in auditor.aborted if f"/api/{target}" in e["event"]]
            hits += [r.split(": ", 1)[0] for r in auditor.refusals if f"/api/{target}" in r]
            return hits

        popup_a, popup_b = accounted("popup-a"), accounted("popup-b")
        aborted_x = accounted("aborted-x")
        refusal_of_aborted = [r for r in auditor.refusals if "/api/aborted-x" in r]

        # The settle window, both polarities. A page whose request never gets an answer has to be named in
        # `unsettled` -- that note is the only thing letting the census tell "the answer arrived as the page
        # went away" from "an observer never saw this session" -- and a page that does go idle must stay out
        # of it, or the note becomes a rubber stamp. The hanging request comes from a route handler that
        # never resolves, so no port or server is involved.
        settle_context = browser.new_context(viewport=VIEWPORTS["desktop"])
        settle_page = settle_context.new_page()
        auditor.arm(settle_page)
        auditor.attach_console(settle_page, "self-test-settle")
        settle_page.route("**/app", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<!doctype html><title>settle</title><p>ok</p>"))
        # Deliberately never resolved: when the context closes over it asyncio prints a CancelledError on
        # stderr. That traceback belongs to this fixture, not to the gate -- the verdict line is the only
        # thing the chain reads, and `unsettled` above is what this arm asserts.
        settle_page.route("**/api/never-answers", lambda route: None)
        settle_page.goto("http://settle.local/app", wait_until="load")
        settle_page.evaluate("() => { fetch('/api/never-answers').catch(() => {}); return 1; }")
        idle_context = browser.new_context(viewport=VIEWPORTS["desktop"])
        idle_page = idle_context.new_page()
        auditor.arm(idle_page)
        auditor.attach_console(idle_page, "self-test-idle")
        idle_page.route("**/calm", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<!doctype html><title>calm</title><p>ok</p>"))
        idle_page.goto("http://settle.local/calm", wait_until="load")
        auditor.settle_context(settle_context, "self-test-settle")
        auditor.settle_context(idle_context, "self-test-idle")
        unsettled_probe = list(auditor.unsettled)
        settled_outstanding = sorted({key for e in unsettled_probe if e["label"] == "self-test-settle"
                                      for key in e["outstanding"]})
        settle_context.close()
        idle_context.close()

        # The refusal census, both polarities, on a real response rather than on a hand-built list: the
        # 403 has to land in `refusals` with its method and path, and the 200 on the next request must
        # not -- a census that recorded everything would let the record claim refusals it never saw.
        before = len(auditor.refusals)
        refuser = browser.new_page()
        auditor.attach_console(refuser, "self-test-refusal")
        refuser.route("**/api/probe-refused",
                      lambda route: route.fulfill(status=403, content_type="text/plain", body="no"))
        refuser.route("**/api/probe-allowed",
                      lambda route: route.fulfill(status=200, content_type="text/plain", body="yes"))
        refusal_error = ""
        try:
            refuser.goto("http://probe.local/api/probe-refused", wait_until="load")
            refuser.goto("http://probe.local/api/probe-allowed", wait_until="load")
        except Exception as exc:
            refusal_error = str(exc)[:120]
        refusal_probe = auditor.refusals[before:]
        browser.close()

    probe = auditor.scans[-1]
    impacts = {v["id"]: v["impact"] for v in probe["violations"]}

    def view(label, **kwargs):
        base = {"label": label, "viewport": "desktop", "axe": True, "audited": True, "settle": "stable",
                "violations": [], "scroll_width": 1440, "client_width": 1440,
                "clipped": 0, "clipped_paths": [], "scroll_containers": 0, "clipped_fields": 0}
        base.update(kwargs)
        return base

    def violation(impact):
        return {"id": f"probe-{impact}", "impact": impact, "node_count": 1, "targets": ["img"]}

    problems = []
    # The origin check, both polarities: it must accept the file it is meant to accept, and it must
    # reject the shape that actually walked into this host's 4173 on 2026-09-26 (a foreign
    # `vite preview` whose first paint has an empty #root and no login form at all).
    ours = (REPO_ROOT / "services" / "web" / "index.html").read_bytes()
    ours_sha = hashlib.sha256(ours).hexdigest()
    if not origin_verdict(ours_sha, ours):
        problems.append("origin check rejects the studio index.html it is supposed to accept")
    intruder = (b"<!doctype html><html lang='zh-CN'><head><title>\xe8\x87\xaa\xe5\x8a\xa8"
                b"\xe5\x87\xba\xe4\xbb\xb7</title><script type='module' src='/@vite/client'>"
                b"</script></head><body><div id='root'></div></body></html>")
    if origin_verdict(ours_sha, intruder):
        problems.append("origin check accepted a foreign single-page app served on the studio port")
    if not probe["audited"]:
        problems.append("the production scan path reported no output on a page axe can evaluate")
    if not any(v.get("why") for v in probe["violations"]):
        problems.append("violation records carry no failureSummary, so a finding would not be actionable")
    if "image-alt" not in impacts:
        problems.append(f"axe did not flag the injected img without alt (saw {sorted(impacts)})")
    if not gate_failures([view("critical", violations=[violation("critical")])]):
        problems.append("gate accepted a critical violation")
    if not gate_failures([view("serious", violations=[violation("serious")])]):
        problems.append("gate accepted a serious violation")
    if not gate_failures([view("moderate", violations=[violation("moderate")])]):
        problems.append("gate accepted a moderate violation, which the criterion now refuses -- the 64 "
                        "left over on 2026-09-27 sat behind exactly this tolerance")
    if gate_failures([view("minor", violations=[violation("minor")])]):
        problems.append("gate fired on a minor-only finding; minor is not in the blocking set, so a policy "
                        "that blocks everything is being mistaken for a policy that blocks moderate")
    if gate_failures([view("clean")]):
        problems.append("gate rejected a clean report")
    if not gate_failures([view("blind", audited=False)]):
        problems.append("gate accepted a scan that reported nothing")
    if gate_failures([view("as-deployed", axe=False, audited=False)]):
        problems.append("gate judged a scan entry that never ran axe")
    if not gate_failures([probe]):
        problems.append("gate accepted the live injected-probe scan")
    if mobile_fit_failures([view("desktop-wide", scroll_width=9999),
                            view("phone-ok", viewport="mobile", scroll_width=390, client_width=390)]):
        problems.append("mobile fit check fired on desktop overflow it should ignore")
    if not mobile_fit_failures([view("phone-wide", viewport="mobile", scroll_width=1200, client_width=390)]):
        problems.append("mobile fit check missed a 1200px page in a 390px viewport")
    if not mobile_fit_failures([view("no-phone-measured", scroll_width=1200, client_width=390)]):
        problems.append("mobile fit check stayed silent when no mobile view was measured")
    clip_named = " ".join(clip_probe["paths"])
    if clip_probe["clipped"] != 1 or "#cut" not in clip_named:
        problems.append(f"clip probe missed the box that hides content (read {clip_probe}, saw {clip_named!r})")
    if "#reach" in clip_named:
        problems.append("clip probe judged a scrollable box clipped, so it would fight real tables")
    if clip_probe["scrollers"] != 1:
        problems.append(f"clip probe counted {clip_probe['scrollers']} scroll containers, so the exemption "
                        "it grants is either vacuous or wider than one box")
    if clip_probe["fields"] != 1 or "#field" in clip_named:
        problems.append(f"clip probe read a form control's own value as withheld content (read {clip_probe}): "
                        "an input scrolls to the caret, which is not a clipped row")
    if clip_failures([view("clip-ok")]):
        problems.append("clip check fired on a page where every box shows what it holds")
    if not clip_failures([view("clip-leak", clipped=1, clipped_paths=["div#cut 300>120"])]):
        problems.append("clip check missed a box holding 300px of content in 120px")
    if not clip_failures([view("clip-blind", clipped=None)]):
        problems.append("clip check stayed silent with an empty denominator")
    if clip_failures([view("clip-blind", clipped=None)], require=False):
        problems.append("clip check's blind case still fired once it was told not to require a reading")
    if not inline_blocked:
        problems.append("inline script executed despite script-src 'self', so the browser is not enforcing CSP")
    if refusal_error:
        problems.append(f"the refusal census could not be exercised on a real response: {refusal_error}")
    if not any("probe-refused" in r and "403" in r and r.split(":")[1].strip().startswith("GET")
               for r in refusal_probe):
        problems.append(f"the refusal census missed a 403 the page really received (read {refusal_probe!r})")
    if any("probe-allowed" in r for r in refusal_probe):
        problems.append("the refusal census recorded a 200 as a refusal, so a count of refusals would not "
                        "mean refusals")
    # The two axes the census needs to attribute a disagreement, each with the polarity that would otherwise
    # make a silent observer look like a working one.
    if popup_a != ["self-test-walk (popup)"]:
        problems.append(f"a page the armed context opened later was not watched under the hook's own label: "
                        f"{popup_a!r} (outcome: {'; '.join(popup_outcome)})")
    if popup_b != ["self-test-popup"]:
        problems.append("the walk's own attach_console either gave the popup a second set of listeners or "
                        f"its label did not win: {popup_b!r}")
    if (auditor.issued["GET /api/popup-a"], auditor.issued["GET /api/popup-b"]) != (1, 1):
        problems.append("the request axis must count each request the armed pages put on the wire exactly "
                        f"once, whatever the answer was (read "
                        f"{ {k: v for k, v in auditor.issued.items() if 'popup-' in k} })")
    if not any(u["label"] == "self-test-settle" for u in unsettled_probe):
        problems.append("a page still holding an unanswered request when it was closed was not named in "
                        f"`unsettled` (read {unsettled_probe!r}), so a shortfall would have no evidence to "
                        "explain it with and would have to be tolerated instead")
    if any(u["label"] == "self-test-idle" for u in unsettled_probe):
        problems.append(f"the page that really did go idle was named as unsettled: {unsettled_probe!r}")
    if any("/api/never-answers" in r for r in auditor.refusals):
        problems.append("a request that was never answered was recorded as a refusal")
    if "GET /api/never-answers" not in settled_outstanding:
        problems.append("the page named unsettled did not also say WHICH requests it was still waiting on, "
                        "so the census would have to excuse a shortfall on the page's word alone "
                        f"(outstanding read {settled_outstanding})")
    try:
        published_probe = json.dumps(unsettled_entries(unsettled_probe))
    except (TypeError, ValueError) as exc:
        published_probe = f"UNPUBLISHABLE: {exc}"
    if published_probe.startswith("UNPUBLISHABLE"):
        problems.append(f"the report could not publish its own teardown notes: {published_probe}")
    if len(unsettled_entries(unsettled_probe + unsettled_probe)) != len(unsettled_probe):
        problems.append("the teardown notes do not de-duplicate, so a page settled twice would be counted "
                        f"twice in the reading the census excuses a shortfall with: {unsettled_probe!r}")
    if any(u["outstanding"] == ["NO-READING"] for u in unsettled_probe):
        problems.append(f"a settle timeout came back with no outstanding reading, which the census reads "
                        f"as no exemption -- it should not happen for a page this run observed: "
                        f"{unsettled_probe!r}")
    if aborted_x != ["self-test-popup"] or refusal_of_aborted:
        problems.append("a request that never got an answer must land on the abort axis and nowhere else "
                        f"(abort axis read {aborted_x!r}, refusal axis read {refusal_of_aborted!r})")
    if auditor.issued["GET /api/aborted-x"]:
        problems.append("the request axis counted a request Playwright answered from the fixture's own "
                        "handler, so the two axes would claim a call the api never saw")
    if csp_failures({"https://x": "script-src 'self'"}, ["https://x"]):
        problems.append("csp check rejected a policy that pins script-src to 'self'")
    if not csp_failures({}, ["https://x"]):
        problems.append("csp check stayed silent for an origin it never observed")
    if not csp_failures({"https://x": "script-src 'self' 'unsafe-inline'"}, ["https://x"]):
        problems.append("csp check accepted unsafe-inline")
    if not csp_failures({"https://x": "default-src 'self'"}, ["https://x"]):
        problems.append("csp check accepted a policy with no script-src directive")
    if hidden_failures([view("hidden-clean", hidden_marked=3)]):
        problems.append("hidden-element check fired on a page where nothing marked hidden renders")
    if not hidden_failures([view("hidden-leak", hidden_marked=3, hidden_but_rendered=["#erasureCodeWrap"])]):
        problems.append("hidden-element check missed an element the app hides by attribute")
    if not hidden_failures([view("hidden-blind", hidden_marked=0)]):
        problems.append("hidden-element check stayed silent with an empty denominator")
    if hidden_failures([view("hidden-blind", hidden_marked=0)], require=False):
        problems.append("hidden-element check demanded a denominator after being told not to")
    if hidden_failures([{"label": "no such key"}], require=False):
        problems.append("hidden-element check requires a key a scan record may legitimately omit")
    if not gate_failures([view("unsettled", settle="timeout")]):
        problems.append("gate accepted a scan taken while the page was still painting")
    if not unstable_during_transition:
        problems.append("the paint sampler called a running 3s colour transition settled")
    if not settled_after:
        problems.append("the paint sampler never reported a finished transition as settled")
    if problems:
        print("SELFCHECK FAILED")
        for p in problems:
            print(" -", p)
        return 1
    print(f"self-check passed: axe-core {AXE_VERSION} flags image-alt (impact={impacts['image-alt']}) "
          f"and the gate rejects it, rejects moderate, and still tolerates minor-only; the clip probe named "
          f"{clip_probe['clipped']} of "
          f"{clip_probe['clipped'] + clip_probe['scrollers'] + clip_probe['fields']} overflowing boxes "
          f"and exempted {clip_probe['scrollers']} scroll container(s) plus {clip_probe['fields']} form field(s)")
    return 0


# ---- the second factor's own surfaces -------------------------------------------------------
# axe has to see the four states the feature adds: the login step, the pending enrolment, the
# recovery list, and the armed panel. The account they run on is created for this run and deleted
# afterwards, because a resident gate must never arm one of the demo accounts -- every other drill
# authenticates those with a password alone, and scripts/e2e_client.py stops with an explicit error
# rather than guessing when a second factor is armed.

def compose_sql(statement: str) -> str:
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin", "-d", "music",
                          "-tAc", " ".join(statement.split())], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""


def api_post(path: str, body: dict, token: str = "") -> dict:
    key = f"POST {url_path(path)}"
    HOST_ISSUED[key] += 1
    request = urllib.request.Request(WEB_URL + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # The api answers a host-side call exactly as it answers a page's, so the census may only read an
        # unaccounted server line as a blind spot once these are on the ledger too. HTTPError is an OSError
        # subclass, so the callers that catch it keep working; this only adds the witness.
        if exc.code >= 400:
            HOST_TIMELINE.append({"ts": _utc_ms(), "label": "host", "event": f"{key} -> {exc.code}"})
        raise


class SecondFactor:
    """A seed, plus the step this run last let through the single-use guard.

    A code is only accepted for a step strictly newer than the last one, so a walk that fires three
    of them back to back has to let the clock move. That waiting is the feature working, not the
    fixture being slow.
    """

    def __init__(self, seed: str, used: int = 0):
        self.seed = seed
        self.used = used

    def code(self, page) -> str:
        from e2e_client import totp_code
        step = int(time.time() // 30)
        while step <= self.used:
            page.wait_for_timeout(500)
            step = int(time.time() // 30)
        self.used = step
        return totp_code(self.seed, at=step * 30 + 5)


def provision_second_factor() -> tuple[str, "SecondFactor"]:
    """Create the probe and arm it over the API, so no scan cycle spends a code to get in."""
    from e2e_client import totp_code
    email = f"a11y-mfa-{time.strftime('%m%dT%H%M%SZ', time.gmtime())}@example.local"
    user_id = compose_sql(f"""INSERT INTO users(email,display_name,password_hash,is_platform_admin)
      SELECT '{email}','A11y MFA Probe',password_hash,false FROM users WHERE email='{EMAIL}' RETURNING id""")
    # The demo owner's workspace, named rather than "the first active one": the isolation suites
    # leave other tenants on this stack and the probe must not appear in any of them.
    compose_sql("INSERT INTO workspace_members(workspace_id,user_id,role) SELECT m.workspace_id,'%s','creator' "
                "FROM workspace_members m JOIN users o ON o.id=m.user_id "
                "WHERE o.email='%s' AND m.role='owner' LIMIT 1" % (user_id, EMAIL))
    token = api_post("/api/auth/login", {"email": email, "password": PASSWORD})["access_token"]
    seed = api_post("/api/auth/mfa/enroll", {}, token)["secret"]
    time.sleep(1)
    api_post("/api/auth/mfa/enroll/verify", {"code": totp_code(seed)}, token)
    return email, SecondFactor(seed, used=int(time.time() // 30))


def retire_second_factor(email: str):
    user_id = compose_sql(f"SELECT id FROM users WHERE email='{email}'")
    if not user_id:
        return
    for table, column in (("auth_sessions", "user_id"), ("user_preferences", "user_id"),
                          ("workspace_members", "user_id"), ("users", "id")):
        compose_sql(f"DELETE FROM {table} WHERE {column}='{user_id}'")


def provision_privacy_probe(email: str) -> dict:
    """A creator-role account in the demo workspace, armed with nothing but a password.

    It has to be somebody other than EMAIL: the walk ends by exercising self-erasure, and the demo
    account is what every other drill on this stack signs in with.
    """
    user_id = compose_sql(f"""INSERT INTO users(email,display_name,password_hash,is_platform_admin)
      SELECT '{email}','A11y Erasure Probe',password_hash,false FROM users WHERE email='{EMAIL}' RETURNING id""")
    if not user_id or email == EMAIL:
        raise RuntimeError("the erasure probe is not a distinct account")
    compose_sql("INSERT INTO workspace_members(workspace_id,user_id,role) SELECT m.workspace_id,'%s','creator' "
                "FROM workspace_members m JOIN users o ON o.id=m.user_id "
                "WHERE o.email='%s' AND m.role='owner' LIMIT 1" % (user_id, EMAIL))
    return {"email": email, "user_id": user_id, "exports": []}


def walk_privacy(page, auditor: Auditor, viewport: str, probe: dict) -> list[str]:
    """Panel -> real download -> confirmation gate -> self-erasure -> receipt on the login screen.

    The erasure is the destructive end of the feature and it is really exercised here: the probe
    account is created for this step and ends anonymised, the same residue scripts/erasure_drill.py
    documents as append-only by design.
    """
    from playwright.sync_api import Error as PlaywrightError

    failures: list[str] = []
    page.wait_for_selector("#login:not([hidden])", timeout=20000)
    page.fill("#email", probe["email"])
    page.fill("#password", PASSWORD)
    page.press("#password", "Enter")
    page.wait_for_selector("#app:not([hidden])", timeout=20000)
    goto_view(page, "数据与账户", "view-account")
    page.wait_for_selector("#erasureTarget", timeout=20000)
    auditor.scan(page, "account-privacy-panel", viewport)
    if not page.locator("#erasureButton").is_disabled():
        failures.append("erasure was clickable before any confirmation was typed")

    # The download has to be a download: a panel that only renders a summary in the DOM would prove
    # nothing about the file a subject actually receives.
    folder = Path(tempfile.mkdtemp(prefix="w3m-privacy-export-"))
    landing = folder / "export.json"
    try:
        with page.expect_download(timeout=30000) as captured:
            page.get_by_role("button", name="下载我的数据").click()
        captured.value.save_as(str(landing))
        name = captured.value.suggested_filename
    except PlaywrightError as exc:
        return [f"the export button produced no download: {str(exc).splitlines()[0]}"]
    if not name.startswith("account-export-"):
        failures.append(f"export download was named {name!r}, not account-export-*")
    bundle = json.loads(landing.read_text(encoding="utf-8"))
    probe["exports"].append({"bytes": landing.stat().st_size, "viewport": viewport})
    if not bundle.get("coverage"):
        failures.append("the downloaded export declared no coverage, so its scope was unverifiable")
    if "password_hash" not in (bundle.get("excluded") or {}):
        failures.append("the downloaded export did not declare password_hash as excluded")
    if "password_hash" in (bundle.get("account") or {}):
        failures.append("the downloaded export carried password_hash despite declaring it excluded")
    if (bundle.get("account") or {}).get("id") != probe["user_id"]:
        failures.append("the export a browser session downloaded was not about that session's own account")
    summary = (page.text_content("#privacyCoverage") or "").strip()
    if "覆盖口径" not in summary:
        failures.append("the export summary never rendered, so the coverage declaration is invisible to a subject")
    auditor.scan(page, "account-privacy-exported", viewport, require="#privacyCoverage p")

    page.fill("#erasureConfirm", probe["email"] + "x")
    if not page.locator("#erasureButton").is_disabled():
        failures.append("a confirmation that does not equal the account email still unlocked erasure")
    page.fill("#erasureConfirm", probe["email"])
    # Typing the email is what a copied session can do; the password is what it cannot. If the button
    # opened on the email alone, the step-up field would be decoration.
    if not page.locator("#erasurePassword").is_visible():
        return failures + ["the erasure panel never asked for a password, so a stolen cookie alone could delete an account"]
    if not page.locator("#erasureButton").is_disabled():
        failures.append("the account email alone unlocked erasure with no password typed")
    if page.locator("#erasureCodeWrap").is_visible():
        failures.append("the panel demanded a second factor this probe never armed")
    page.fill("#erasurePassword", "not-the-password")
    if page.locator("#erasureButton").is_disabled():
        return failures + ["typing a password never enabled the button, so no subject can self-delete"]
    page.get_by_role("button", name="永久删除我的账户").click()
    page.wait_for_selector("#erasureError:not(:empty)", timeout=30000)
    refusal = (page.text_content("#erasureError") or "").strip()
    if "password" not in refusal.lower():
        failures.append(f"a wrong password was refused without the reason reaching the panel: {refusal[:120]!r}")
    auditor.scan(page, "account-privacy-erasure-refused", viewport, require="#erasureError")
    if page.locator("#erasedNotice:not([hidden])").count():
        return failures + ["a wrong password still erased the account"]
    shown = (page.text_content("#erasureTarget") or "").strip()
    if shown != probe["email"]:
        return failures + [f"the panel was showing {shown!r} as the erasure target, not the probe"]
    page.fill("#erasurePassword", PASSWORD)
    page.get_by_role("button", name="永久删除我的账户").click()
    try:
        page.wait_for_selector("#erasedNotice:not([hidden])", timeout=30000)
    except PlaywrightError:
        return failures + ["after a successful erasure the app never showed the receipt on the login screen"]
    receipt = (page.text_content("#erasedNoticeText") or "").strip()
    if "账户已删除" not in receipt:
        failures.append(f"the erasure receipt said something odd: {receipt[:120]!r}")
    auditor.scan(page, "login-erased-receipt", viewport, require="#erasedNoticeText")
    # Cookies are host-scoped, not port-scoped: the session cookie the erased probe left behind would
    # otherwise be handed to the admin origin in this same context, and its first /api/auth/me would
    # log "session has been revoked or expired" -- real behaviour, but fixture noise, since the two
    # apps never share a host in a deployment. The cookie is HttpOnly, so only the test can drop it.
    page.context.clear_cookies()

    # The account is gone, so the same credentials must now be refused on the same form.
    page.fill("#email", probe["email"])
    page.fill("#password", PASSWORD)
    page.press("#password", "Enter")
    page.wait_for_selector("#loginError:not(:empty)", timeout=20000)
    if page.locator("#app:not([hidden])").count():
        failures.append("the erased account signed back in, so access was not actually cut")
    return failures


def provision_team_probe(email: str) -> str:
    """An account that exists on the platform but belongs to no workspace, so it can be offered one."""
    return compose_sql(f"""INSERT INTO users(email,display_name,password_hash,is_platform_admin)
      SELECT '{email}','A11y Team Probe',password_hash,false FROM users WHERE email='{EMAIL}' RETURNING id""")


def retire_team_probe(user_id: str):
    if not user_id:
        return
    for table, column in (("workspace_invitations", "used_by"), ("workspace_invitations", "revoked_by"),
                          ("workspace_members", "user_id"), ("auth_sessions", "user_id"),
                          ("user_preferences", "user_id"), ("users", "id")):
        try:
            compose_sql(f"DELETE FROM {table} WHERE {column}='{user_id}'")
        except SystemExit as exc:
            # A walk that aborted mid-cycle can leave a row another table still points at; that is
            # teardown noise, not a finding, so report it and keep going rather than turn the gate red.
            print(f"  team probe cleanup skipped on {table}: {str(exc)[:120]}")


def reload_account_view(page, auditor: Auditor, viewport: str):
    """Re-open 数据与账户 and wait for the panel's own reads to come back, not just for the view class.

    navigate() flips `.active` synchronously and only then awaits its fetches, so a check written
    against the DOM immediately after the click reads the previous render. Both roster lists are waited
    for by response, which is the only signal that says "the numbers on screen are from this visit".
    """
    goto_view(page, "资产", "view-assets")
    with page.expect_response(lambda r: "/api/workspace/members" in r.url, timeout=20000) as roster, \
            page.expect_response(lambda r: "/api/workspace/invitations" in r.url, timeout=20000) as offers:
        goto_view(page, "数据与账户", "view-account")
    for awaited in (roster, offers):
        response = awaited.value
        if response.status != 200:
            raise SystemExit(f"re-entering the account view answered {response.status} on {response.url}")


def sign_in_as(page, email: str):
    """Log a second account in on its own page, without the state audit login() performs.

    login() exists to produce an auditable login screen, and the owner's page already did that this
    run; re-auditing the same state from the invitee's page would only add a second axe pass over an
    identical document. What the invitee's page has to contribute is a session, so this is the short
    version of the same handshake.
    """
    page.goto(WEB_URL, wait_until="networkidle")
    page.wait_for_selector("#loginForm", timeout=20000)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.press("#password", "Enter")
    page.wait_for_selector("#app:not([hidden])", timeout=20000)


def pending_offers(page) -> int:
    """How many offers the owner's panel currently counts as live, read off its own state line."""
    match = re.match(r"(\d+) 份待接受", (page.text_content("#inviteState") or "").strip())
    if not match:
        raise SystemExit(f"the offer panel's state line does not count live offers: {page.text_content('#inviteState')!r}")
    return int(match.group(1))


def offer_row(page, address: str):
    """The owner's offer row for exactly this address.

    Matched on the element that holds the address rather than on the row's text: a row's
    ``textContent`` concatenates the address with whatever span follows it
    (``nobody-here-x@example.localDemo Owner 发出 · …``), so a whole-token regex can never match, while
    a plain substring locator collides the probe address with its own ``nobody-here-`` twin and
    Playwright's strict mode refuses the pair. ``:text-is`` is exact per element, which is the identity
    both rows actually have.
    """
    return page.locator(f"#inviteList .member-row:has(b:text-is('{address}'))")


def member_row(page, address: str):
    """The roster row for exactly this address, for the same reason as offer_row."""
    return page.locator(f"#teamList .member-row:has(span:text-is('{address}'))")


def walk_team(page, invitee, auditor: Auditor, viewport: str, probe_email: str) -> list[str]:
    """Offer -> the addressed account accepts in its own session -> re-role -> confirm dialog -> remove.

    The order is the claim: nothing on the owner's side of this walk can make the probe a member, so
    the roster is asserted not to have moved while only offers existed, and it moves exactly once, in
    the invitee's session.
    """
    failures: list[str] = []
    goto_view(page, "数据与账户", "view-account")
    page.wait_for_selector("#teamList .member-row", timeout=20000)
    # The state line is the panel's own count, so it is read rather than assumed -- but only as a
    # baseline: aborted runs of this gate leave offers behind, so every later expectation is a delta.
    page.wait_for_function("() => /^\\d+ 份待接受/.test((document.querySelector('#inviteState')||{}).textContent||'')",
                           timeout=20000)
    before = page.locator("#teamList .member-row").count()
    live = pending_offers(page)
    auditor.scan(page, "workspace-team", viewport)

    ghost = "nobody-here-" + probe_email
    page.fill("#teamInviteEmail", ghost)
    page.select_option("#teamInviteRole", "viewer")
    page.get_by_role("button", name="发出邀请").click()
    offer_row(page, ghost).first.wait_for(timeout=20000)
    # The old panel answered an unknown address with a refusal, which is what made it a membership
    # probe; the offer now goes out the same way it does for a known account, so an empty error strip
    # and a rendered one-time token are the assertions, not a message to look for.
    if (page.text_content("#teamError") or "").strip():
        failures.append(f"inviting an address with no account still surfaces a refusal: {page.text_content('#teamError')!r}")
    if not page.locator("#teamInviteToken").is_visible():
        failures.append("the offer came back with no one-time token, so there is nothing to hand over out of band")
    elif len((page.text_content("#teamInviteTokenValue") or "").strip()) < 20:
        failures.append(f"the token the panel shows is too short to be the one the server issued: {page.text_content('#teamInviteTokenValue')!r}")
    if not page.locator("#teamInviteExpiry").is_visible():
        failures.append("the panel offered an invitation without saying when it stops working")
    auditor.scan(page, "workspace-team-offered", viewport)
    if page.locator("#teamList .member-row").count() != before:
        failures.append("an offer to an address nobody owns still changed the roster")

    page.fill("#teamInviteEmail", probe_email)
    page.select_option("#teamInviteRole", "viewer")
    page.get_by_role("button", name="发出邀请").click()
    offer_row(page, probe_email).first.wait_for(timeout=20000)
    if page.locator("#teamList .member-row").count() != before:
        failures.append("the roster grew before the addressed account ever said yes -- the offer attached somebody")
    auditor.scan(page, "workspace-team-pending", viewport)

    if pending_offers(page) != live + 2:
        failures.append(f"two offers were issued but the panel counts {pending_offers(page) - live} new live ones")
    offer_row(page, ghost).get_by_role("button", name="撤销").click()
    page.wait_for_selector("#dialog[open]", timeout=20000)
    # Taking back one's own offer is not a step-up action: it removes nothing a person already holds.
    # Asking for the password here would be the panel confusing "destructive" with "irreversible".
    if page.locator("#dialog .dialog-fields input[type=password]").count():
        failures.append("withdrawing an offer now demands a credential, which it deliberately does not need")
    auditor.scan(page, "workspace-team-revoke-dialog", viewport)
    page.locator("#dialog .dialog-submit").click()
    offer_row(page, ghost).first.locator(".tag:has-text('已撤销')").wait_for(timeout=20000)
    if offer_row(page, ghost).count() != 1:
        failures.append("a withdrawn offer vanished from the list instead of staying as history")
    if offer_row(page, ghost).locator("button").count() != 0:
        failures.append("a withdrawn offer still carries a button")
    if pending_offers(page) != live + 1:
        failures.append(f"withdrawing one of the two offers left the panel counting {pending_offers(page) - live} live")

    # This is the moment the invitation feature is actually tested as usable: the account signing in
    # here holds no membership at all, so a boot that requires one -- /api/bootstrap answering 400 and
    # init() throwing the visitor back to the form -- makes this line fail rather than pass quietly.
    sign_in_as(invitee, probe_email)
    goto_view(invitee, "数据与账户", "view-account")
    invitee.wait_for_selector("#inboxList .member-row", timeout=20000)
    if invitee.locator("#inboxList .member-row").count() != 1:
        failures.append(f"the addressed account sees {invitee.locator('#inboxList .member-row').count()} offers, "
                        "expected the one live offer and not the withdrawn one")
    if "nobody-here-" in (invitee.text_content("#inboxList") or ""):
        failures.append("the withdrawn offer is still readable in the invitee's inbox")
    auditor.scan(invitee, "workspace-team-inbox", viewport)
    accept = invitee.locator("#inboxList .member-row").first.get_by_role("button", name="接受", exact=True)
    accept.click()
    # The panel repeats, in the confirmation, whose credential the database is about to compare. That
    # sentence is the feature: an offer is not a seat until the addressed account spends its own session.
    invitee.wait_for_selector("#dialog[open]", timeout=20000)
    dialog_text = invitee.text_content("#dialog") or ""
    if "邮箱" not in dialog_text or "登录账户" not in dialog_text:
        failures.append(f"the acceptance dialog does not say whose address the database will check: {dialog_text[:160]!r}")
    auditor.scan(invitee, "workspace-team-accept-dialog", viewport)
    invitee.locator("#dialog .dialog-submit").click()
    # "The row went away" is not the evidence: a claim that the server refused also leaves an empty
    # list, by way of the panel's error state. An acceptance has to land as 没有待处理 with nothing in
    # the error strip, or the owner-side checks below would be reading a screen that never changed.
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    try:
        invitee.wait_for_function(
            "() => !document.querySelector('#inboxList .member-row')"
            " && !((document.querySelector('#inboxError')||{}).textContent || '').trim()"
            " && /没有待处理/.test((document.querySelector('#inboxState')||{}).textContent || '')",
            timeout=20000)
    except PlaywrightTimeout:
        failures.append("the acceptance never read as settled on the invitee's own screen: "
                        f"state={invitee.text_content('#inboxState')!r} error={invitee.text_content('#inboxError')!r}")

    reload_account_view(page, auditor, viewport)
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    try:
        member_row(page, probe_email).first.wait_for(timeout=20000)
    except PlaywrightTimeout:
        failures.append("the acceptance never reached the owner's roster (panel says "
                        f"{(page.text_content('#teamState') or '').strip()!r}, "
                        f"rows={page.locator('#teamList .member-row').count()}, baseline={before})")
    try:
        offer_row(page, probe_email).first.locator(".tag:has-text('已接受')").wait_for(timeout=20000)
    except PlaywrightTimeout:
        failures.append("the accepted offer is not shown as settled on the owner's side: "
                        f"{(offer_row(page, probe_email).first.text_content() or '')[:160]!r}")
    auditor.scan(page, "workspace-team-accepted", viewport)
    if page.locator("#teamList .member-row").count() != before + 1:
        failures.append(f"the roster moved by {page.locator('#teamList .member-row').count() - before} rows for one acceptance")

    row = member_row(page, probe_email)
    # The confirmation dialogs now carry two fields, so a bare `input` selector would match both and
    # Playwright's strict mode would fail the walk for the wrong reason.
    email_field = "#dialog .dialog-fields input:not([type=password])"
    password_field = "#dialog .dialog-fields input[type=password]"

    # Re-roling someone is a privilege change, so the panel asks for the password in a dialog before
    # the PATCH goes out. The dialog is scanned because it is the one surface that traps focus.
    row.locator("select").select_option("reviewer")
    row.get_by_role("button", name="保存角色").click()
    page.wait_for_selector("#dialog[open]", timeout=20000)
    auditor.scan(page, "workspace-team-role-dialog", viewport)
    page.fill(password_field, PASSWORD)
    page.locator("#dialog .dialog-submit").click()
    member_row(page, probe_email).first.locator(".tag:text-is('reviewer')").wait_for(timeout=20000)
    auditor.scan(page, "workspace-team-role", viewport)

    row.get_by_role("button", name="移出").click()
    page.wait_for_selector("#dialog[open]", timeout=20000)
    auditor.scan(page, "workspace-team-remove-dialog", viewport)
    if page.locator(password_field).count() != 1:
        failures.append("the removal dialog carries no password field, so the email alone still authorises it")
    page.fill(email_field, "wrong@" + probe_email)
    page.fill(password_field, PASSWORD)
    page.locator("#dialog .dialog-submit").click()
    page.wait_for_selector("#dialog[open]", state="detached", timeout=20000)
    mismatch = (page.text_content("#teamError") or "").strip()
    if "不一致" not in mismatch:
        failures.append("a confirmation that does not match the member's e-mail still let removal proceed")
    if member_row(page, probe_email).count() != 1:
        failures.append("the mismatched confirmation removed the member anyway")

    # The blank run is the point of the feature: naming the target's e-mail -- which the panel already
    # shows on screen -- must not be enough. The dialog stays open and says why.
    row.get_by_role("button", name="移出").click()
    page.wait_for_selector("#dialog[open]", timeout=20000)
    page.fill(email_field, probe_email)
    page.locator("#dialog .dialog-submit").click()
    if not page.locator("#dialog[open]").count():
        failures.append("the removal dialog submitted with the password left empty")
    if not (page.text_content("#dialog .dialog-error") or "").strip():
        failures.append("an empty password left no message in the dialog")
    else:
        auditor.scan(page, "workspace-team-remove-blank", viewport, require="#dialog .dialog-error")
    if member_row(page, probe_email).count() != 1:
        failures.append("an empty password still removed the member")
    page.fill(password_field, PASSWORD)
    page.locator("#dialog .dialog-submit").click()
    member_row(page, probe_email).first.wait_for(state="detached", timeout=20000)
    auditor.scan(page, "workspace-team-removed", viewport)
    if page.locator("#teamList .member-row").count() != before:
        failures.append("the roster never returned to the size it started at")
    return failures
NON_ADMIN_EMAIL = os.environ.get("E2E_NON_ADMIN_EMAIL", "third@example.local")
NON_ADMIN_PASSWORD = os.environ.get("E2E_NON_ADMIN_PASSWORD", "demo-viewer")


def walk_roster(page, auditor: Auditor, viewport: str) -> list[str]:
    """The control plane's workspace directory, as the platform administrator sees and expands it.

    The reconciliation that matters here is on the screen: the card declares a member count and the
    roster panel lists the members, and a directory that counts from one query while the panel reads
    another would still look perfectly populated. Both halves are rendered from 018's functions, so
    this is the only place the two statements are compared against each other.
    """
    failures: list[str] = []
    goto_view(page, "工作区与成员", "view-workspaces")
    cards = page.locator("#workspaceDirectory .gate-card")
    page.wait_for_selector("#workspaceDirectory .gate-card", timeout=20000)
    count = cards.count()
    auditor.scan(page, "admin-workspaces", viewport, require="#workspaceDirectory .gate-card")
    if count < 2:
        failures.append(f"the directory listed {count} workspace card(s); the seeded platform has at least two")
    if page.locator("#workspaceDirectory .workspace-roster-btn").count() != count:
        failures.append("a workspace card has no roster button, so part of the directory is unreachable")

    declared, target = 0, None
    for index in range(count):
        text = cards.nth(index).text_content() or ""
        found = re.search(r"成员 (\d+) 人", text)
        if found and int(found.group(1)) > 0:
            declared, target = int(found.group(1)), index
            break
    if target is None:
        return failures + ["no workspace card declared a member above zero, so the roster panel was never exercised"]
    button = cards.nth(target).locator(".workspace-roster-btn")
    if not (button.get_attribute("aria-label") or "").startswith("载入 "):
        failures.append(f"the roster button is announced as {button.get_attribute('aria-label')!r}, which does not name whose roster it loads")
    button.click()
    page.wait_for_selector("#workspaceRoster", state="visible", timeout=20000)
    page.wait_for_selector("#workspaceRoster .table-row", timeout=20000)
    rows = page.locator("#workspaceRoster .table-row").count()
    auditor.scan(page, "admin-workspaces-roster", viewport, require="#workspaceRoster .table-row")
    if rows != declared:
        failures.append(f"the card said 成员 {declared} 人 while the roster panel listed {rows} row(s)")
    # Read-only by design: membership writes belong to the tenant's own panel, where the actor types a
    # credential. A button in here would mean the platform can re-role a tenant's member anonymously.
    if page.locator("#workspaceRoster button, #workspaceRoster input, #workspaceRoster select").count():
        failures.append("the platform roster panel carries a write control, which it is not meant to have")
    return failures


def walk_stale_panels(browser, size, auditor: Auditor, viewport: str) -> list[str]:
    """One panel's load answer 500: the app must stay up and that panel must say its numbers are stale.

    This is the case refreshAll() guards. Measured against the live stack, a non-administrator is not it
    -- /api/admin/v12/{dashboard,payments,moderation}, /api/admin/dashboard and /api/jobs all answer 200
    for any logged-in member, and only /payouts and the new /workspaces are platform-administrator
    routes -- so the guard is exercised by injecting the failure instead of by looking for a session
    that happens to produce one.

    Its own browser context, because these sessions share an origin: a page opened in the context the
    refusal walk just used boots already signed in, and login() then waits on a hidden #loginForm.
    """
    failures: list[str] = []
    served = []
    context = browser.new_context(viewport=size)
    page = context.new_page()

    def injected(route):
        served.append(route.request.url)
        route.fulfill(status=500, content_type="application/json",
                      body='{"detail":"injected by the accessibility gate"}')

    auditor.arm(page)
    # Registered after arm() and verified by the `served` premise below: with two matching routes, the
    # one registered last is the one Playwright consults first. Measured the other way round, the
    # auditor's catch-all swallowed this request and the injected failure never happened -- which is
    # exactly the sort of green-that-means-nothing the check underneath refuses to report.
    page.route("**/api/admin/v12/moderation", injected)
    auditor.attach_console(page, f"{viewport}-stale-admin")
    try:
        login(page, ADMIN_URL, auditor, viewport)
        page.wait_for_selector("#app:not([hidden])", timeout=20000)
        if not served:
            # The order in which Playwright consults two matching routes is the whole premise here: if
            # the auditor's catch-all ran first, the response was a real 200 and every assertion below
            # would be reading a healthy panel.
            return ["the injected 500 never reached the request, so the stale-panel path was not exercised"]
        goto_view(page, "信任与支持", "view-trust")
        page.wait_for_selector("#cases .muted", timeout=20000)
        auditor.scan(page, "admin-trust-stale", viewport, require="#cases .muted")
        note = page.locator("#cases").text_content() or ""
        if "未能载入" not in note:
            failures.append(f"the refused panel did not label itself stale: {note[:160]!r}")
        if page.locator("#tickets .muted").count() != 1:
            failures.append("the second container of the same loader kept its previous render")
        # The panels that did load must keep their numbers: a guard that blanks the whole console turns a
        # partial failure into a fake emergency.
        if page.locator("#stats .stat").count() == 0:
            failures.append("a single failed load blanked the overview too, so the operator cannot tell "
                            "which figures are current")
    finally:
        auditor.settle_context(context, f"{viewport}-stale-admin")
        context.close()
    return failures


def walk_roster_refusal(browser, size, auditor: Auditor, viewport: str) -> list[str]:
    """The same view for an account that is logged in but is not the platform's administrator.

    The workspace view is one of exactly two platform-administrator surfaces in this console, so this is
    the only place its refusal copy is reachable -- and reaching it depends on refreshAll() no longer
    letting one bad load hide #app. Its own context, for the same session-sharing reason as above.
    """
    failures: list[str] = []
    context = browser.new_context(viewport=size)
    page = context.new_page()
    auditor.arm(page)
    auditor.attach_console(page, f"{viewport}-refusal-admin")
    try:
        login(page, ADMIN_URL, auditor, viewport, email=NON_ADMIN_EMAIL, password=NON_ADMIN_PASSWORD)
        goto_view(page, "工作区与成员", "view-workspaces")
        page.wait_for_selector("#workspaceDirectory :not(script)", timeout=20000)
        panel = page.locator("#workspaceDirectory").text_content() or ""
        auditor.scan(page, "admin-workspaces-refusal", viewport, require="#workspaceDirectory")
        if "仅平台管理员可访问" not in panel:
            failures.append(f"the workspace view for a non-administrator read: {panel[:120]!r}")
        if page.locator("#workspaceDirectory .gate-card").count():
            failures.append("a non-administrator was shown real workspace cards")
        # The rest of the console is deliberately NOT refused for this account, and saying so here keeps
        # the refusal honest: an operator should see which one grid they may not read, not a blank app.
        if page.locator("#stats .stat").count() == 0:
            failures.append("the non-administrator's own workspace figures disappeared with the platform view")
    finally:
        auditor.settle_context(context, f"{viewport}-refusal-admin")
        context.close()
    return failures


def walk_mfa(page, auditor: Auditor, viewport: str, probe: "SecondFactor", email: str):
    """Login step -> armed panel -> drop it -> enrolment -> recovery list. Ends armed again."""
    page.wait_for_selector("#login:not([hidden])", timeout=20000)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.press("#password", "Enter")
    page.wait_for_selector("#mfaStep:not([hidden])", timeout=20000)
    auditor.scan(page, "login-second-factor", viewport)
    page.fill("#mfaCode", probe.code(page))
    page.get_by_role("button", name="验证并登录").click()
    page.wait_for_selector("#app:not([hidden])", timeout=20000)
    goto_view(page, "数据与账户", "view-account")
    page.wait_for_selector("#mfaArmed:not([hidden])", timeout=20000)
    auditor.scan(page, "account-mfa-armed", viewport)

    # Dropping the second factor is the first move an attacker makes with a stolen session, so the
    # panel now wants the password alongside the code. The blank attempt is what proves the field is
    # load-bearing rather than decorative -- and it must not spend the code shown on screen.
    page.fill("#mfaDisableCode", probe.code(page))
    page.get_by_role("button", name="关闭两步验证").click()
    page.wait_for_selector("#mfaError:not(:empty)", timeout=20000)
    blank = (page.text_content("#mfaError") or "").strip()
    if "密码" not in blank:
        raise SystemExit(f"the panel dropped the factor without asking for the password: {blank[:120]!r}")
    auditor.scan(page, "account-mfa-refused", viewport, require="#mfaError")
    page.fill("#mfaDisablePassword", PASSWORD)
    page.fill("#mfaDisableCode", probe.code(page))
    page.get_by_role("button", name="关闭两步验证").click()
    page.wait_for_selector("#mfaState:text-is('未开启')", timeout=20000)
    page.get_by_role("button", name="开启两步验证").click()
    page.wait_for_selector("#mfaEnrol:not([hidden])", timeout=20000)
    probe.seed = (page.text_content("#mfaSecret") or "").strip()
    # The QR is the enrolment path most people use, so the browser itself has to rasterize it: a
    # naturalWidth of 0 means the data: URI the CSP allows was not an image the engine could decode,
    # and an empty alt would make axe report it as a critical finding on this very page.
    page.wait_for_selector("#mfaQr:not([hidden])", timeout=20000)
    qr_state = page.evaluate("""() => { const i = document.querySelector('#mfaQr');
        return {w: i.naturalWidth, h: i.naturalHeight, alt: (i.alt || '').trim(), src: i.src.slice(0, 22)}; }""")
    if not (qr_state["w"] > 0 and qr_state["h"] == qr_state["w"]):
        raise SystemExit(f"the enrolment QR did not rasterize in the browser: {qr_state}")
    if not qr_state["alt"]:
        raise SystemExit("the enrolment QR has no alternative text, so a screen reader gets nothing")
    if qr_state["src"] != "data:image/png;base64,":
        raise SystemExit(f"the enrolment QR is not the PNG data URI the CSP allows: {qr_state['src']!r}")
    auditor.scan(page, "account-mfa-enrolment", viewport)

    page.fill("#mfaEnrolCode", probe.code(page))
    page.get_by_role("button", name="确认开启").click()
    page.wait_for_selector("#mfaRecovery:not([hidden])", timeout=20000)
    if page.locator("#mfaRecoveryList code").count() != 10:
        raise SystemExit("the recovery list did not render ten codes, so that state was not exercised")
    auditor.scan(page, "account-mfa-recovery", viewport)
    page.get_by_role("button", name="退出").click()
    page.wait_for_selector("#login:not([hidden])", timeout=20000)


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or "unavailable"
    except Exception:
        return "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="prove the audit and the gate can fire")
    parser.add_argument("--allow-blocking", type=int, default=0, help="tolerate up to N blocking findings and still exit 0")
    parser.add_argument("--desktop-only", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test(Auditor(axe_source(), Path(tempfile.mkdtemp(prefix="w3m-a11y-selftest-"))))

    out_dir = Path("release-evidence") / f"browser-a11y-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    auditor = Auditor(axe_source(), out_dir)

    from playwright.sync_api import sync_playwright

    # Audited with axe present, in the axe stage only -- the same scoping the keyboard checks use.
    # The probe is armed here so that reaching the login step costs no code inside the scan loop.
    probe = probe_email = None
    try:
        probe_email, probe = provision_second_factor()
    except (SystemExit, OSError, ValueError) as exc:
        print(f"second-factor probe could not be provisioned ({exc}); the four MFA states will not be audited")
        probe = probe_email = None
    if probe_email:
        atexit.register(retire_second_factor, probe_email)

    keyboard: list[str] = []
    privacy: list[str] = []
    team: list[str] = []
    roster: list[str] = []
    boot: list[str] = []
    boot_walks = 0
    roster_walks = 0
    team_probes: list[str] = []
    privacy_probes: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Identity is checked from inside the browser, before any state is walked: which process owns
        # a dual-stack port is decided by the resolver that is about to drive it, and a green run
        # against somebody else's app would certify nothing.
        gate = browser.new_context().new_page()
        try:
            preflight_origins(gate)
        finally:
            gate.context.close()
        for viewport, size in VIEWPORTS.items():
            if viewport == "mobile" and args.desktop_only:
                continue
            for stage in ("as-deployed", "axe"):
                auditor.run_axe = stage == "axe"
                auditor.relax_csp = stage == "axe"
                context = browser.new_context(viewport=size)
                page = context.new_page()
                auditor.arm(page)
                auditor.attach_console(page, f"{viewport}-{stage}-studio")
                login(page, WEB_URL, auditor, viewport)
                walk_studio(page, auditor, viewport)
                walk_views(page, auditor, viewport)
                if stage == "axe":
                    # The roster walk runs in two sessions -- the demo owner's, and the probe's own --
                    # and cleans up after itself through the UI, so the probe account only has to be
                    # removed here.
                    team_email = (f"a11y-team-{viewport}-"
                                  f"{time.strftime('%m%dT%H%M%SZ', time.gmtime())}@example.local").lower()
                    team_user = ""
                    try:
                        team_user = provision_team_probe(team_email)
                    except (SystemExit, OSError, ValueError) as exc:
                        team.append(f"team probe could not be provisioned: {str(exc)[:160]}")
                    if team_user:
                        team_probes.append(team_email)
                        # Registered at creation, not after the walk: two aborted runs today left probe
                        # accounts in the demo roster, and atexit is what turns "I created it" into
                        # "I remove it" even when the walk raises on the way.
                        atexit.register(retire_team_probe, team_user)
                        # The second context is the point, not a convenience: cookies are per-context,
                        # and the claim being walked is that an offer only becomes a membership when the
                        # addressed account asks for it. One page cannot be both the inviter and the
                        # person invited.
                        invitee_context = browser.new_context(viewport=size)
                        invitee = invitee_context.new_page()
                        auditor.arm(invitee)
                        auditor.attach_console(invitee, f"{viewport}-{stage}-invitee")
                        try:
                            team += walk_team(page, invitee, auditor, viewport, team_email)
                        finally:
                            auditor.settle_context(invitee_context, f"{viewport}-invitee")
                            invitee_context.close()
                        retire_team_probe(team_user)
                if stage == "axe":
                    keyboard += keyboard_checks(page, auditor, viewport)
                    boot += walk_boot_click(browser, size, auditor, viewport)
                    boot_walks += 1
                page.get_by_role("button", name="退出").click()
                page.wait_for_selector("#login:not([hidden])", timeout=20000)
                if stage == "axe" and probe:
                    walk_mfa(page, auditor, viewport, probe, probe_email)
                if stage == "axe":
                    # Not best-effort: a run that could not provision the erasure probe has not
                    # exercised the destructive half of the privacy surface, and must say so in red.
                    try:
                        privacy_probe = provision_privacy_probe(
                            f"a11y-erasure-{viewport}-{time.strftime('%m%dT%H%M%SZ', time.gmtime())}@example.local")
                    except (SystemExit, OSError, ValueError) as exc:
                        privacy_probe = None
                        privacy.append(f"erasure probe could not be provisioned: {str(exc)[:160]}")
                    if privacy_probe:
                        privacy_probes.append(privacy_probe)
                        privacy += walk_privacy(page, auditor, viewport, privacy_probe)
                admin = context.new_page()
                auditor.arm(admin)
                auditor.attach_console(admin, f"{viewport}-{stage}-admin")
                login(admin, ADMIN_URL, auditor, viewport)
                if stage == "axe":
                    auditor.scan(admin, "admin-overview", viewport, require="#stats *")
                    walk_views(admin, auditor, viewport, nav="adminNav")
                    roster += walk_roster(admin, auditor, viewport)
                    roster_walks += 1
                admin.get_by_role("button", name="退出").click()
                admin.wait_for_selector("#login:not([hidden])", timeout=20000)
                if stage == "axe":
                    # Two more sessions on purpose, each in its own context: one where the account really
                    # is not the administrator, one where a panel's load really fails.
                    roster += walk_roster_refusal(browser, size, auditor, viewport)
                    roster += walk_stale_panels(browser, size, auditor, viewport)
                auditor.settle_context(context, f"{viewport}-{stage}")
                context.close()
        browser.close()
    if probe_email:
        retire_second_factor(probe_email)

    mobile_scans = sum(1 for s in auditor.scans if s["viewport"] == "mobile")
    exports = [e for probe in privacy_probes for e in probe["exports"]]
    expected_walks = 1 if args.desktop_only else 2
    privacy = privacy + (
        [] if len(privacy_probes) >= expected_walks else
        [f"the privacy walk ran on {len(privacy_probes)} of {expected_walks} viewports, "
         "so self-erasure was not exercised where it was skipped"])
    team = team + (
        [] if len(team_probes) >= expected_walks else
        [f"the team walk ran on {len(team_probes)} of {expected_walks} viewports, "
         "so membership writes were not exercised where it was skipped"])
    roster = roster + (
        [] if roster_walks >= expected_walks else
        [f"the roster walk ran on {roster_walks} of {expected_walks} viewports, "
         "so the platform directory was not exercised where it was skipped"])
    boot = boot + (
        [] if boot_walks >= expected_walks else
        [f"the boot-window click ran on {boot_walks} of {expected_walks} viewports, "
         "so navigation during loading was not exercised where it was skipped"])
    failures = (gate_failures(auditor.scans)
                + sorted(set(privacy))
                + sorted(set(team))
                + sorted(set(roster))
                + sorted(set(boot))
                + mobile_fit_failures(auditor.scans, require=not args.desktop_only)
                + clip_failures(auditor.scans)
                + keyboard + csp_failures(auditor.security_headers, [WEB_URL, ADMIN_URL])
                + hidden_failures(auditor.scans)
                + sorted(set(auditor.crashes)) + sorted(set(auditor.csp_blocks)))
    auditor.account_host_calls()
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit(),
        "mobile_fit_measured": mobile_scans,
        "boot_window_clicks": boot_walks,
        "web_url": WEB_URL,
        "admin_url": ADMIN_URL,
        "axe_core": AXE_VERSION,
        "axe_sha256": AXE_SHA256,
        "blocking_impacts": list(BLOCKING_IMPACTS),
        "views_scanned": len(auditor.scans),
        "second_factor_states": sorted({s["label"] for s in auditor.scans
                                        if "mfa" in s["label"] or "second-factor" in s["label"]}),
        "second_factor_probe": probe_email,
        "privacy_states": sorted({s["label"] for s in auditor.scans
                                  if "privacy" in s["label"] or "erased" in s["label"]}),
        "privacy_exports": exports,
        "privacy_probe_accounts": sorted(p["email"] for p in privacy_probes),
        "team_states": sorted({s["label"] for s in auditor.scans if "team" in s["label"]}),
        "team_probe_accounts": sorted(team_probes),
        "roster_states": sorted({s["label"] for s in auditor.scans
                                 if s["label"].startswith("admin-workspaces") or s["label"] == "admin-trust-stale"}),
        "roster_walks": roster_walks,
        "axe_scans": sum(1 for s in auditor.scans if s["axe"]),
        "violations_by_impact": auditor.summary(),
        "content_security_policy": auditor.security_headers,
        "csp_blocked_inline_styles": sorted(set(auditor.csp_blocks)),
        "uncaught_errors": sorted(set(auditor.crashes)),
        "uncaught_events": len(auditor.crashes),
        "console_errors": sorted(set(auditor.errors)),
        "console_error_events": len(auditor.errors),
        "refusals": sorted(set(auditor.refusals)),
        "refusal_events": len(auditor.refusals),
        # Per-endpoint counts without the label prefix, because `refusals` is deduplicated and so cannot
        # answer "how many times" -- which is the question the server-side census asks back.
        "refusal_counts": {tail: n for tail, n in sorted(
            collections.Counter(r.split(": ", 1)[-1] for r in auditor.refusals).items())},
        # The timeline is what lets a disagreement with the api access log be attributed rather than argued
        # about: `refusal_counts` says how many, this says when and from which page.
        "refusal_timeline": sorted(auditor.refusal_timeline, key=lambda e: (e["ts"], e["label"])),
        # The other half of the attribution: the refusal axis can only count answers a page was handed,
        # while this counts requests the auditor itself put on the wire -- before any response, abort or
        # teardown could hide one. `server_refusal_census.py` compares it against every request line the api
        # logged for a refusing endpoint, which is what separates "the page never got the answer" from
        # "something outside the armed pages was talking to the api".
        "request_counts": {tail: n for tail, n in sorted(auditor.issued.items())},
        "request_events": sum(auditor.issued.values()),
        "aborted_requests": sorted({e["event"] for e in auditor.aborted}),
        "aborted_events": len(auditor.aborted),
        "unsettled_at_close": unsettled_entries(auditor.unsettled),
        "abort_timeline": sorted(auditor.aborted, key=lambda e: (e["ts"], e["label"])),
        "gate_started_at": auditor.started_at,
        "gate_last_refusal_at": max((e["ts"] for e in auditor.refusal_timeline), default=None),
        "scans": auditor.scans,
        "failures": failures,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"scanned {len(auditor.scans)} views with axe-core {AXE_VERSION}")
    print(f"privacy walk: {len(exports)} exports downloaded and parsed, "
          f"{len(privacy_probes)} probe accounts erased ({', '.join(p['email'] for p in privacy_probes) or 'none'})")
    print(f"team walk: {len(team_probes)} roster walks with offer/accept/re-role/revoke/remove exercised "
          f"({', '.join(sorted(team_probes)) or 'none'})")
    print(f"boot-window click: {boot_walks} single nav click(s) issued while /api/bootstrap was "
          f"still held, each required to switch the view")
    print(f"roster walk: {roster_walks} platform-admin directory walks, each followed by a non-administrator "
          f"refusal and an injected panel failure "
          f"({', '.join(report['roster_states']) or 'none'})")
    for impact, count in sorted(auditor.summary().items()):
        print(f"  {impact}: {count} rule(s)")
    if auditor.errors:
        print(f"  console/page errors: {len(set(auditor.errors))} line(s) out of {len(auditor.errors)} event(s)")
        for err in sorted(set(auditor.errors))[:8]:
            print(f"    - {err}")
    print(f"  refusals seen by the pages: {len(set(auditor.refusals))} line(s) out of "
          f"{len(auditor.refusals)} event(s)")
    for line in sorted(set(auditor.refusals)):
        print(f"    - {line}")
    print(f"  requests the auditor put on the wire: {report['request_events']} event(s), of which "
          f"{report['aborted_events']} never got an answer back")
    for line in report["aborted_requests"]:
        print(f"    - no response: {line}")
    print(f"report: {out_dir / 'report.json'}")

    tolerable = failures[: args.allow_blocking]
    hard = failures[args.allow_blocking:]
    for line in tolerable:
        print(f"KNOWN (allowed by --allow-blocking): {line}")
    if hard:
        print(f"FAIL: {len(hard)} blocking finding(s)")
        for line in hard:
            print(f"  - {line}")
        return 1
    print("browser a11y + walkthrough passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
