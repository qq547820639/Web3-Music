from .utils import sha256_json

CAPABILITY_NAMES = ["stream","download","share","commercial_use","license","sublicense","distribute","mint","content_id"]


def build_manifest(asset: dict, provider_snapshot: dict, contribution_summary: dict | None = None) -> dict:
    provider = provider_snapshot.get("provider", "unknown")
    approval = provider_snapshot.get("approval_status", "unknown")
    contribution_summary = contribution_summary or {}
    capabilities = {name: {"status":"unknown","reason":"No approved contract evidence"} for name in CAPABILITY_NAMES}

    if provider == "emulator":
        capabilities["stream"] = {"status":"allowed","reason":"Local development media"}
        capabilities["download"] = {"status":"allowed","reason":"Personal archive in local development"}
        capabilities["share"] = {"status":"manual_review","reason":"Emulator output is not a commercial music right"}
        for name in ["commercial_use","license","sublicense","distribute","mint","content_id"]:
            capabilities[name] = {"status":"blocked","reason":"Provider Emulator is development-only"}
    elif approval == "approved_commercial":
        for name in ["stream","download","share","commercial_use","distribute"]:
            capabilities[name] = {"status":"allowed","reason":"Approved provider capability snapshot"}
        for name in ["license","sublicense","mint","content_id"]:
            capabilities[name] = {"status":"manual_review","reason":"Requires product-specific legal review"}
    else:
        capabilities["stream"] = {"status":"manual_review","reason":"Provider approval is not production-approved"}
        capabilities["download"] = {"status":"manual_review","reason":"Provider approval is not production-approved"}

    manifest = {
        "version": 1,
        "status": "development_only" if provider == "emulator" else "unverified",
        "provider": provider,
        "provider_approval": approval,
        "asset_snapshot_id": str(asset["id"]),
        "media_hash": asset["media_hash"],
        "human_contribution": contribution_summary,
        "ai_disclosure": "fully_or_partly_generated_ai",
        "capabilities": capabilities,
        "disclaimer": "This manifest records platform evidence and capabilities; it is not a copyright registration or legal opinion."
    }
    manifest["manifest_hash"] = sha256_json(manifest)
    return manifest


def allowed(manifest: dict, capability: str) -> bool:
    return manifest.get("capabilities", {}).get(capability, {}).get("status") == "allowed"
