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
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

AXE_VERSION = "4.13.0"
AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"
AXE_TGZ = f"https://registry.npmjs.org/axe-core/-/axe-core-{AXE_VERSION}.tgz"
CACHE = Path(os.environ.get("AXE_CACHE_DIR", ".cache")) / f"axe-core-{AXE_VERSION}.min.js"

WEB_URL = os.environ.get("WEB_URL", "http://localhost:4173")
ADMIN_URL = os.environ.get("ADMIN_URL", "http://localhost:4174")
EMAIL = os.environ.get("E2E_EMAIL", "owner@example.local")
PASSWORD = os.environ.get("E2E_PASSWORD", "demo-owner")
VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}
BLOCKING_IMPACTS = ("critical", "serious")
BLOCKING_CONSOLE = re.compile(r"uncaught|refused to execute|would have been blocked|failed to fetch", re.I)
CSP_BLOCK = re.compile(r"violates the following content security policy|refused to apply inline style", re.I)

# axe reads computed colours at the instant it runs, so a button caught mid
# transition yields an interpolated ratio no user ever settles on. Two identical
# paint samples a full transition-length apart (the app transitions background in
# .18s) is what "as deployed" means for a colour check.
PAINT_SIG_JS = """() => Array.from(document.querySelectorAll('body *')).slice(0, 400)
  .map(e => { const c = getComputedStyle(e); return c.color + '/' + c.backgroundColor; }).join(';')"""


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


class Auditor:
    axe_path = "/__axe/axe.min.js"

    def __init__(self, axe: str, out_dir: Path, run_axe: bool = True, relax_csp: bool = False):
        self.axe = axe
        self.out_dir = out_dir
        self.run_axe = run_axe
        self.relax_csp = relax_csp
        self.scans: list[dict] = []
        self.errors: list[str] = []
        self.crashes: list[str] = []
        self.csp_blocks: list[str] = []
        self.security_headers: dict[str, str] = {}

    def arm(self, page):
        page.route(re.compile(".*"), self._route)

    def _route(self, route):
        request = route.request
        if request.url.endswith(self.axe_path):
            route.fulfill(status=200, content_type="application/javascript", body=self.axe)
            return
        if self.relax_csp and request.resource_type == "document":
            # axe injects its own styles; under the shipped style-src 'self' it is
            # blinded and reports bogus contrast failures. Strip the policy only for
            # the audit pass — the as-deployed pass keeps it and asserts on it.
            fetched = route.fetch()
            headers = {k: v for k, v in fetched.headers.items() if k.lower() != "content-security-policy"}
            route.fulfill(status=fetched.status, body=fetched.body(), headers=headers)
            return
        route.continue_()

    def record_headers(self, origin: str, response):
        if self.relax_csp or not response or response.status != 200:
            return
        self.security_headers[origin] = response.headers.get("content-security-policy", "<absent>")

    def attach_console(self, page, label: str):
        def on_console(message):
            if message.type != "error":
                return
            entry = f"{label}: {message.text}"
            self.errors.append(entry)
            if CSP_BLOCK.search(message.text):
                self.csp_blocks.append(entry)
            if BLOCKING_CONSOLE.search(message.text):
                self.crashes.append(entry)

        page.on("console", on_console)
        page.on("pageerror", lambda e: self.crashes.append(f"{label}: uncaught exception: {e}"))

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


def login(page, base: str, auditor: Auditor, viewport: str, tries: int = 5):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    response = page.goto(base, wait_until="networkidle")
    auditor.record_headers(base, response)
    auditor.scan(page, "login", viewport, require="#loginForm")
    detail = ""
    for attempt in range(tries):
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.press("#password", "Enter")
        try:
            page.wait_for_selector("#app:not([hidden])", timeout=10000)
            page.wait_for_function(
                "() => { const pill = document.querySelector('#creditPill'); return !pill || /\\d/.test(pill.textContent); }",
                timeout=20000)
            return
        except PlaywrightTimeout:
            detail = login_error(page)
            # The limiter is per account (login:sha256(email), limit 10), and this suite
            # authenticates the same owner eight times. Measured against the live API,
            # every attempt — refused ones included — refreshes the 60s window, so the
            # wait has to exceed it; a 12s or 30s retry never gets back in.
            page.wait_for_timeout(75000 if RATE_LIMITED.search(detail) else 3000)
    raise SystemExit(f"login never succeeded after {tries} attempts: {detail or 'no error text'}")


def press_until(page, button, expect: str, what: str, tries: int = 12):
    """Handlers are bound only at the end of boot (bindNavigation runs after the
    first data load), so a click can land on an inert button. Retry until the
    expected state shows up instead of assuming the page was ready."""
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
        browser.close()

    probe = auditor.scans[-1]
    impacts = {v["id"]: v["impact"] for v in probe["violations"]}

    def view(label, **kwargs):
        base = {"label": label, "viewport": "desktop", "axe": True, "audited": True, "settle": "stable",
                "violations": [], "scroll_width": 1440, "client_width": 1440}
        base.update(kwargs)
        return base

    def violation(impact):
        return {"id": f"probe-{impact}", "impact": impact, "node_count": 1, "targets": ["img"]}

    problems = []
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
    if gate_failures([view("moderate", violations=[violation("moderate")])]):
        problems.append("gate fired on a moderate-only report, which the documented criterion tolerates")
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
    if not inline_blocked:
        problems.append("inline script executed despite script-src 'self', so the browser is not enforcing CSP")
    if csp_failures({"https://x": "script-src 'self'"}, ["https://x"]):
        problems.append("csp check rejected a policy that pins script-src to 'self'")
    if not csp_failures({}, ["https://x"]):
        problems.append("csp check stayed silent for an origin it never observed")
    if not csp_failures({"https://x": "script-src 'self' 'unsafe-inline'"}, ["https://x"]):
        problems.append("csp check accepted unsafe-inline")
    if not csp_failures({"https://x": "default-src 'self'"}, ["https://x"]):
        problems.append("csp check accepted a policy with no script-src directive")
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
          f"and the gate rejects it while tolerating moderate-only")
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
    request = urllib.request.Request(WEB_URL + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode())


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

    page.fill("#mfaDisableCode", probe.code(page))
    page.get_by_role("button", name="关闭两步验证").click()
    page.wait_for_selector("#mfaState:text-is('未开启')", timeout=20000)
    page.get_by_role("button", name="开启两步验证").click()
    page.wait_for_selector("#mfaEnrol:not([hidden])", timeout=20000)
    probe.seed = (page.text_content("#mfaSecret") or "").strip()
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

    keyboard: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
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
                    keyboard += keyboard_checks(page, auditor, viewport)
                page.get_by_role("button", name="退出").click()
                page.wait_for_selector("#login:not([hidden])", timeout=20000)
                if stage == "axe" and probe:
                    walk_mfa(page, auditor, viewport, probe, probe_email)
                admin = context.new_page()
                auditor.arm(admin)
                auditor.attach_console(admin, f"{viewport}-{stage}-admin")
                login(admin, ADMIN_URL, auditor, viewport)
                if stage == "axe":
                    auditor.scan(admin, "admin-overview", viewport, require="#stats *")
                    walk_views(admin, auditor, viewport, nav="adminNav")
                admin.get_by_role("button", name="退出").click()
                admin.wait_for_selector("#login:not([hidden])", timeout=20000)
                context.close()
        browser.close()
    if probe_email:
        retire_second_factor(probe_email)

    mobile_scans = sum(1 for s in auditor.scans if s["viewport"] == "mobile")
    failures = (gate_failures(auditor.scans)
                + mobile_fit_failures(auditor.scans, require=not args.desktop_only)
                + keyboard + csp_failures(auditor.security_headers, [WEB_URL, ADMIN_URL])
                + sorted(set(auditor.crashes)) + sorted(set(auditor.csp_blocks)))
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit(),
        "mobile_fit_measured": mobile_scans,
        "web_url": WEB_URL,
        "admin_url": ADMIN_URL,
        "axe_core": AXE_VERSION,
        "axe_sha256": AXE_SHA256,
        "blocking_impacts": list(BLOCKING_IMPACTS),
        "views_scanned": len(auditor.scans),
        "second_factor_states": sorted({s["label"] for s in auditor.scans
                                        if "mfa" in s["label"] or "second-factor" in s["label"]}),
        "second_factor_probe": probe_email,
        "axe_scans": sum(1 for s in auditor.scans if s["axe"]),
        "violations_by_impact": auditor.summary(),
        "content_security_policy": auditor.security_headers,
        "csp_blocked_inline_styles": sorted(set(auditor.csp_blocks)),
        "uncaught_errors": sorted(set(auditor.crashes)),
        "console_errors": sorted(set(auditor.errors)),
        "scans": auditor.scans,
        "failures": failures,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"scanned {len(auditor.scans)} views with axe-core {AXE_VERSION}")
    for impact, count in sorted(auditor.summary().items()):
        print(f"  {impact}: {count} rule(s)")
    if auditor.errors:
        print(f"  console/page errors: {len(set(auditor.errors))}")
        for err in sorted(set(auditor.errors))[:8]:
            print(f"    - {err}")
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
