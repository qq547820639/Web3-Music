"""Static guards on the subject-rights surface: the browser panel and the two endpoints it calls.

The live half of this (a real download, a real self-erasure, the receipt after it) is
scripts/browser_a11y.py's `walk_privacy`, which needs a stack. These guards need none, and they
catch the failures a browser walk only reports as a timeout: the panel pointing at a path the API
does not serve, a handler reaching for an element no document defines, wire data reaching
innerHTML, an expected 401 back in the error channel, or the erasure POST quietly becoming
CSRF-exempt. Every helper is also run against a planted violation, because a needle that cannot
match reads exactly like a clean tree.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JS = ROOT / "services/web/app.js"
ADMIN_JS = ROOT / "services/admin/admin.js"
INDEX_HTML = ROOT / "services/web/index.html"
AUTH_PY = ROOT / "services/api/app/auth.py"
MIGRATION = ROOT / "db/migrations/013_user_erasure.sql"

PRIVACY_PATHS = ("/api/account/export", "/api/account/erasure")
PANEL_IDS = ("privacyState", "privacyExport", "privacyCoverage", "privacyError",
             "erasureTarget", "erasureConfirm", "erasureButton", "erasureError",
             "erasedNotice", "erasedNoticeText", "erasedDismiss")


def text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def served_paths() -> set[str]:
    """The route table as the API itself reports it, not a list copied into this test."""
    sys.path.insert(0, str(ROOT / "services/api"))
    from app.main import app
    return {route.path for route in app.routes if getattr(route, "path", "")}


def paths_referenced(source: str) -> set[str]:
    # Capture the path only: quoting the opening delimiter into the match is how every
    # membership test against it silently reads as absent.
    return set(re.findall(r"['\"`](/api/[a-z0-9_/{}.$-]+)", source))


def ids_declared(*sources: str) -> set[str]:
    """Both documents mint elements: index.html by hand, app.js from its view templates."""
    return {found for source in sources for found in re.findall(r'id="([A-Za-z0-9_-]+)"', source)}


def dangling_selectors(source: str, declared: set[str]) -> set[str]:
    return set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", source)) - declared


def role_of(document: str, element_id: str) -> str:
    match = re.search(rf'<[^>]*id="{element_id}"[^>]*>', document)
    assert match, f"{element_id} is not in the document"
    found = re.search(r'role="([^"]+)"', match.group(0))
    return found.group(1) if found else ""


def csrf_exempt_paths(source: str) -> set[str]:
    """The literal set inside validate_browser_csrf, read from the guard rather than restated."""
    start = source.index("def validate_browser_csrf")
    match = re.search(r"request\.url\.path in \{([^}]*)\}", source[start:start + 2500], re.S)
    assert match, "the CSRF exemption is no longer a set literal this guard can read"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_the_panel_calls_paths_the_api_actually_serves():
    referenced = paths_referenced(text(APP_JS))
    missing = [path for path in PRIVACY_PATHS if path not in referenced]
    assert not missing, f"the subject-rights panel no longer calls: {missing}"
    unserved = [path for path in PRIVACY_PATHS if path not in served_paths()]
    assert not unserved, f"the browser calls paths the API does not route: {unserved}"
    # Control: the route lookup must be able to tell a served path from an invented one, and the
    # reader must be able to see the shape it is asked about.
    assert "/api/account/definitely-not-a-route" not in served_paths()
    assert paths_referenced("await api('/api/account/erasure', { method: 'POST' });") == {"/api/account/erasure"}


def test_the_confirmation_guard_matches_the_database_predicate():
    # The client unlocks the button on an exact email match; the guarantee is the 013 predicate.
    # Both halves are pinned so neither can drift away from the other unnoticed.
    assert re.search(r"confirmation !== \(state\.user\?\.email \|\| ''\)", text(APP_JS)), \
        "the erasure button is no longer gated on the account email"
    assert re.search(r"coalesce\(confirmation,''\) <> account\.email", text(MIGRATION)), \
        "erase_user_identity stopped comparing the confirmation to the stored email"


def innerhtml_lines(block: str) -> list[str]:
    """Code lines that name innerHTML. A comment about the rule must not read as breaking it --
    this file's own prose says the word, so an unfiltered scan reports itself as a violation."""
    return [stripped for stripped in (line.strip() for line in block.splitlines())
            if "innerHTML" in stripped and not stripped.startswith(("//", "*", "/*"))]


def test_privacy_renderers_keep_wire_data_out_of_innerhtml():
    block = text(APP_JS).split("function privacyTarget()")[1].split("async function loadAccount()")[0]
    assert len(block) > 1500, "the extractor found no privacy block, so it could not have failed"
    offenders = innerhtml_lines(block)
    assert not offenders, f"the privacy renderers write markup from responses: {offenders}"
    assert innerhtml_lines("// innerHTML is not used here\nbox.innerHTML = payload;") == ["box.innerHTML = payload;"]


def test_every_element_the_privacy_handlers_touch_exists():
    declared = ids_declared(text(INDEX_HTML), text(APP_JS))
    dangling = sorted(dangling_selectors(text(APP_JS), declared))
    assert not dangling, f"app.js selects elements no document defines: {dangling}"
    missing = sorted(set(PANEL_IDS) - ids_declared(text(INDEX_HTML)))
    assert not missing, f"the subject-rights panel lost elements from index.html: {missing}"
    # Control: a selector with no backing element must be reported, not read as clean.
    assert dangling_selectors("$('#ghostPanel').onclick = noop;", {"login"}) == {"ghostPanel"}


def test_privacy_feedback_regions_are_announced():
    document = text(INDEX_HTML)
    # Failures answer the user (alert); progress and receipts stay polite (status).
    for element_id, expected in (("privacyError", "alert"), ("erasureError", "alert"),
                                 ("privacyCoverage", "status"), ("erasedNotice", "status"),
                                 ("loginError", "alert")):
        assert role_of(document, element_id) == expected, f"{element_id} is mis-announced"
    assert role_of('<div id="quiet"></div>', "quiet") == "", "the reader treats a missing role as alert"


def test_the_erasure_post_is_not_csrf_exempt():
    exempt = csrf_exempt_paths(text(AUTH_PY))
    assert "/api/account/erasure" not in exempt, f"a cross-site form could delete an account: {sorted(exempt)}"
    assert "/api/account/export" not in exempt, "the export must stay a same-origin read"
    assert "/api/auth/login" in exempt, "the exemption reader stopped finding the list it guards"
    planted = 'def validate_browser_csrf(request):\n    if request.url.path in {"/api/account/erasure"}:\n        return\n'
    assert "/api/account/erasure" in csrf_exempt_paths(planted), "the reader cannot see an added exemption"


def test_an_expected_401_stays_out_of_the_browser_error_channel():
    """A signed-out cold load is normal; logging it as a fault is what filled the console channel."""
    for name, source, guard in (("app.js", text(APP_JS), r"if \(err && err\.status !== 401\) console\.error\(err\);"),
                                ("admin.js", text(ADMIN_JS), r"if \(e && e\.status !== 401\) console\.error\(e\);")):
        assert re.search(guard, source), f"{name} logs every first-paint failure again"
        sites = re.findall(r"[^;]*console\.error\(", source)
        unguarded = [site for site in sites if "status !== 401" not in site]
        assert not unguarded, f"{name} has an unguarded console.error: {unguarded}"
    assert re.search(r"e\.status = r\.status;", text(ADMIN_JS)), \
        "admin.js does not attach the status its 401 guard reads, so that guard could never fire"
