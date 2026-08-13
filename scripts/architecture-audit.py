#!/usr/bin/env python3
"""Dependency-light architecture regression checks for the v13 final product."""
from pathlib import Path
import json
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]


def require(path: str, needles: list[str]) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise SystemExit(f"{path}: missing required contracts: {missing}")


require("db/migrations/001_production_candidate.sql", [
    "CREATE TABLE credit_holds", "CREATE TABLE generation_jobs", "CREATE TABLE ledger_transactions",
    "CREATE CONSTRAINT TRIGGER ledger_must_balance", "ALTER TABLE %I FORCE ROW LEVEL SECURITY",
    "CREATE TABLE domain_outbox", "CREATE TABLE provider_inbox", "generation_job_matches_quote",
    "asset_snapshot_consistent",
])
require("db/migrations/002_creation_asset_market_os.sql", [
    "CREATE TABLE project_branches", "CREATE TABLE ai_runs", "CREATE TABLE rights_evidence",
    "CREATE TABLE orders", "request_hash char(64) NOT NULL", "CREATE TABLE refunds",
    "CREATE TABLE licenses", "CREATE TABLE brand_briefs", "CREATE TABLE support_tickets",
    "CREATE TABLE offer_reservations", "CREATE TABLE revenue_splits", "CREATE TABLE payouts",
    "marketplace_offers_public", "licensed_delivery_package", "reserve_marketplace_offer",
    "record_license_revenue", "reverse_license_revenue", "rights_manifests_workspace_asset_id_key",
    "commercial_use'->>'status','unknown')='allowed'", "CREATE POLICY party_policy ON licenses",
])
require("db/migrations/003_final_hardening.sql", [
    "subscriptions_workspace_order_fk", "readable_templates", "enforce_payout_transition",
    "REVOKE UPDATE,DELETE ON payouts FROM music_app",
])
require("db/migrations/004_brand_award_commercial_flow.sql", [
    "ux_active_brand_award_order", "prepare_brand_award", "submission rights are not licensable",
])
require("services/worker/worker.py", [
    "FOR UPDATE SKIP LOCKED", "lease_expires_at", "heartbeat_at", "audio decode validation failed",
    "validate_url(current)", "close_hold", "start_metrics_server", "ai_music_worker_jobs_terminal_total",
])
require("services/api/app/main.py", [
    "Idempotency-Key", "require_platform_admin", "verify_media_token", "X-Workspace-Id",
    "validate_song_spec", "Resonance AI Music Asset Platform", "product_layers", "render_metrics",
])
require("services/api/app/metrics.py", ["ai_music_api_http_requests_total", "_duration_sum"])
require("services/api/app/domain/quality.py", [
    "v12-reference-28d-2.1-final", "PAC_n", "TSMI_n", "NSRQ_n", "C3AC_n", "SoftPenalty",
])
require("services/api/app/routers/market.py", [
    "payment idempotency key was reused", "refund idempotency key was reused", "payment_inbox",
    "licensed_delivery_package", "X-Package-SHA256", "assert_offer_rights", "reserve_marketplace_offer",
    "record_license_revenue", "reverse_license_revenue", "brand-submissions/{submission_id}/award",
])
require("services/api/app/routers/assets.py", [
    "provider contract does not permit commercial capabilities",
    "commercial capabilities require selected provider contract or license evidence",
    "rights.evidence.submit", "legal_hold",
])
require("services/api/app/auth.py", ["workspace membership required", "is_platform_admin", "jwt.decode", "auth_sessions", "CSRF validation failed"])
require("services/api/app/domain/ledger.py", ["operation_key", "FOR UPDATE", "settlement exceeds remaining hold"])
require("services/payment-emulator/main.py", ["idempotency key reused", "refund exceeds payment amount"])
require("services/provider-emulator/main.py", ['"async":True', "partial_success", "idempotency key reused"])
require("db/migrations/005_final_release.sql", ["CREATE TABLE auth_sessions", "CREATE TABLE moderation_decisions", "G13"])
require("services/api/app/domain/moderation.py", ["unauthorized_voice_clone", "artist_style_reference", "evaluate_policy"])
require("services/worker/provider.py", ["class GenericRESTAdapter", "GENERIC_PROVIDER_BASE_URL", "Idempotency-Key"])
require("services/gateway/nginx.conf", ["limit_req_zone", "location /admin/", "location /api/"])
require("docker-compose.yml", ["payment-emulator", "prometheus", "acceptance", "METRICS_PORT", "http://127.0.0.1:9101/health"])

all_code = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "services").rglob("*.py"))
for forbidden in ["X-User-Id", "BRPOP generation_jobs", 'allow_origins=["*"]', "balance = balance -", "__duration_sum"]:
    if forbidden in all_code:
        raise SystemExit(f"forbidden legacy pattern found: {forbidden}")

for migration_path in sorted((ROOT / "db/migrations").glob("*.sql")):
    migration = migration_path.read_text(encoding="utf-8")
    if re.search(r",\s*\);", migration):
        raise SystemExit(f"{migration_path.name}: trailing comma before closing parenthesis")

compose=yaml.safe_load((ROOT/'docker-compose.yml').read_text(encoding='utf-8'))
required_services={"postgres","redis","minio","migrate","provider-emulator","payment-emulator","api","worker","prometheus","web","admin","gateway","acceptance"}
missing_services=required_services-set(compose.get('services',{}))
if missing_services: raise SystemExit(f"compose missing services: {sorted(missing_services)}")

schema = json.loads((ROOT / "shared/contracts/openapi-v13.json").read_text(encoding="utf-8"))
if schema.get("info", {}).get("version") != "13.0.0" or len(schema.get("paths", {})) < 60:
    raise SystemExit("OpenAPI v13 is stale or incomplete")
required_paths = {
    "/api/projects/{project_id}/chat", "/api/orders/{order_id}/pay", "/api/payment-webhooks/{provider}",
    "/api/deliveries/{delivery_id}/export", "/api/brand-briefs", "/api/admin/v12/release-gate/{gate}",
    "/api/marketplace/offers/{offer_id}/purchase", "/api/admin/v12/payouts/{payout_id}",
    "/api/brand-submissions/{submission_id}/award",
}
missing = sorted(required_paths - set(schema["paths"]))
if missing:
    raise SystemExit(f"OpenAPI missing paths: {missing}")
print("architecture contracts valid")
