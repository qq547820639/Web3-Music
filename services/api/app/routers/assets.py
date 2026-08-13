from __future__ import annotations

import uuid
from typing import Any

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import Actor, require_roles
from ..common import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, audit, serialize, setting_enabled
from ..db import fetch_all, fetch_one, transaction
from ..domain.commerce import canonical_hash

router = APIRouter(prefix="/api", tags=["Asset OS"])

CAPABILITIES = ("stream", "download", "share", "commercial_use", "license", "sublicense", "distribute", "mint", "content_id")
STATUSES = {"allowed", "blocked", "manual_review", "unknown"}


class EvidenceCreate(BaseModel):
    evidence_type: str = Field(pattern="^(user_declaration|license|consent|provider_contract|source_file|identity|other)$")
    project_id: str | None = None
    evidence: dict[str, Any]
    expires_at: str | None = None


class RightsReview(BaseModel):
    status: str = Field(pattern="^(verified|restricted|disputed|development_only|unverified)$")
    capabilities: dict[str, dict[str, str]]
    legal_hold: bool = False
    review_note: str = Field(min_length=3, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)


class CaseCreate(BaseModel):
    subject_type: str = Field(pattern="^(project|candidate|asset|user|brand_brief)$")
    subject_id: str
    case_type: str = Field(pattern="^(copyright|voice_likeness|trademark|harassment|minor_safety|malware|other)$")
    severity: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    evidence: dict[str, Any] = Field(default_factory=dict)
    legal_hold: bool = False


class CaseUpdate(BaseModel):
    status: str = Field(pattern="^(open|triage|restricted|appealed|resolved|dismissed)$")
    restrictions: dict[str, Any] = Field(default_factory=dict)
    legal_hold: bool = False


def _asset(asset_id: str, actor: Actor):
    row = fetch_one(
        "SELECT * FROM asset_snapshots WHERE id=%s AND workspace_id=%s",
        (asset_id, actor.workspace_id),
        actor.workspace_id,
    )
    if not row:
        raise HTTPException(404, "asset not found")
    return row


@router.get("/assets")
def list_assets(limit: int = Query(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX), offset: int = Query(default=0, ge=0), q: str | None = Query(default=None), status: str | None = Query(default=None), actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal"))):
    where = "a.workspace_id=%s"
    params: list[Any] = [actor.workspace_id]
    if q:
        where += " AND (p.title ILIKE %s OR a.id::text ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if status:
        where += " AND m.status=%s"
        params.append(status)
    params += [limit, offset]
    rows = fetch_all(
        f"""
        SELECT a.id,a.project_id,a.spec_revision,a.snapshot,a.snapshot_hash,a.media_hash,a.created_at,
               p.title,m.id AS rights_manifest_id,m.version AS rights_version,m.status AS rights_status,m.manifest,
               EXISTS(SELECT 1 FROM moderation_cases c WHERE c.workspace_id=a.workspace_id AND c.subject_type='asset' AND c.subject_id=a.id::text AND c.legal_hold) AS legal_hold,
               COUNT(*) OVER()::int AS total
        FROM asset_snapshots a
        JOIN song_projects p ON p.id=a.project_id
        LEFT JOIN LATERAL (
          SELECT * FROM rights_manifests r WHERE r.asset_snapshot_id=a.id ORDER BY r.version DESC LIMIT 1
        ) m ON true
        WHERE {where} ORDER BY a.created_at DESC LIMIT %s OFFSET %s
        """,
        tuple(params),
        actor.workspace_id,
    )
    return serialize({"items": rows, "total": rows[0]["total"] if rows else 0, "limit": limit, "offset": offset})


@router.get("/assets/{asset_id}/provenance")
def asset_provenance(asset_id: str, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal"))):
    asset = _asset(asset_id, actor)
    project = fetch_one("SELECT * FROM song_projects WHERE id=%s AND workspace_id=%s", (asset["project_id"], actor.workspace_id), actor.workspace_id)
    revision = fetch_one(
        "SELECT * FROM song_spec_revisions WHERE project_id=%s AND revision=%s AND workspace_id=%s",
        (asset["project_id"], asset["spec_revision"], actor.workspace_id),
        actor.workspace_id,
    )
    candidate = fetch_one(
        """
        SELECT c.*,m.sha256,m.mime_type,m.bytes,m.duration_ms,m.scan_status,j.provider_snapshot_id,j.provider_job_id,j.created_at AS generated_at
        FROM audio_candidates c JOIN media_assets m ON m.id=c.media_asset_id JOIN generation_jobs j ON j.id=c.job_id
        WHERE c.id=%s AND c.workspace_id=%s
        """,
        (asset["candidate_id"], actor.workspace_id),
        actor.workspace_id,
    )
    contributions = fetch_all(
        "SELECT * FROM contribution_events WHERE project_id=%s AND workspace_id=%s AND (spec_revision IS NULL OR spec_revision<=%s) ORDER BY created_at",
        (asset["project_id"], actor.workspace_id, asset["spec_revision"]),
        actor.workspace_id,
    )
    manifests = fetch_all(
        "SELECT * FROM rights_manifests WHERE asset_snapshot_id=%s AND workspace_id=%s ORDER BY version",
        (asset_id, actor.workspace_id),
        actor.workspace_id,
    )
    evidence = fetch_all(
        "SELECT * FROM rights_evidence WHERE asset_snapshot_id=%s AND workspace_id=%s ORDER BY created_at",
        (asset_id, actor.workspace_id),
        actor.workspace_id,
    )
    return serialize({
        "asset": asset,
        "project": project,
        "revision": revision,
        "candidate": candidate,
        "contributions": contributions,
        "rights_manifests": manifests,
        "rights_evidence": evidence,
    })


@router.get("/assets/{asset_id}/rights-evidence")
def list_evidence(asset_id: str, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "legal"))):
    _asset(asset_id, actor)
    return serialize(fetch_all(
        "SELECT e.*,u.display_name AS submitted_by_name FROM rights_evidence e JOIN users u ON u.id=e.submitted_by WHERE e.asset_snapshot_id=%s AND e.workspace_id=%s ORDER BY e.created_at",
        (asset_id, actor.workspace_id),
        actor.workspace_id,
    ))


@router.post("/assets/{asset_id}/rights-evidence", status_code=201)
def add_evidence(asset_id: str, body: EvidenceCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "legal"))):
    asset = _asset(asset_id, actor)
    if body.project_id and str(asset["project_id"]) != body.project_id:
        raise HTTPException(409, "project does not match asset")
    evidence_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO rights_evidence(id,workspace_id,project_id,asset_snapshot_id,evidence_type,evidence,submitted_by,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (evidence_id, actor.workspace_id, asset["project_id"], asset_id, body.evidence_type, psycopg2.extras.Json(body.evidence), actor.user_id, body.expires_at),
        )
        row = cur.fetchone()
        audit(cur, actor, "rights.evidence.submit", "rights_evidence", evidence_id, {"asset_id": asset_id, "type": body.evidence_type}, request.state.request_id)
    return serialize(row)


@router.post("/assets/{asset_id}/rights-review", status_code=201)
def issue_reviewed_manifest(asset_id: str, body: RightsReview, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "legal"))):
    asset = _asset(asset_id, actor)
    unknown = set(body.capabilities) - set(CAPABILITIES)
    if unknown:
        raise HTTPException(400, f"unknown capabilities: {sorted(unknown)}")
    normalized = {}
    for name in CAPABILITIES:
        value = body.capabilities.get(name, {"status": "unknown", "reason": "Not reviewed"})
        if value.get("status") not in STATUSES:
            raise HTTPException(400, f"invalid status for {name}")
        normalized[name] = {"status": value["status"], "reason": value.get("reason", "")[:1000]}
    if body.legal_hold:
        for name in ("download", "share", "commercial_use", "license", "sublicense", "distribute", "mint", "content_id"):
            normalized[name] = {"status": "blocked", "reason": "Asset is under Legal Hold"}
    evidence = fetch_all(
        "SELECT id,status,evidence_type,expires_at FROM rights_evidence WHERE workspace_id=%s AND asset_snapshot_id=%s AND id=ANY(%s::uuid[]) AND status NOT IN ('rejected','expired') AND (expires_at IS NULL OR expires_at>now())",
        (actor.workspace_id, asset_id, body.evidence_ids or []),
        actor.workspace_id,
    ) if body.evidence_ids else []
    if len(evidence) != len(set(body.evidence_ids)):
        raise HTTPException(400, "one or more evidence ids are invalid, rejected or expired")
    requested_commercial = any(normalized[name]["status"] == "allowed" for name in ("commercial_use", "license", "sublicense", "distribute", "mint", "content_id"))
    if requested_commercial and not any(e["evidence_type"] in {"provider_contract", "license"} for e in evidence):
        raise HTTPException(409, "commercial capabilities require selected provider contract or license evidence")
    previous = fetch_one(
        "SELECT * FROM rights_manifests WHERE asset_snapshot_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1",
        (asset_id, actor.workspace_id),
        actor.workspace_id,
    )
    if previous and (previous["provider"] == "emulator" or previous["provider_approval"] in {"development_only", "unapproved", "unknown"}):
        if requested_commercial:
            raise HTTPException(409, "provider contract does not permit commercial capabilities; legal review cannot override provider rights")
    version = int(previous["version"] if previous else 0) + 1
    manifest = {
        "version": version,
        "status": body.status,
        "asset_snapshot_id": asset_id,
        "media_hash": asset["media_hash"],
        "provider": previous["provider"] if previous else "unknown",
        "provider_approval": previous["provider_approval"] if previous else "unknown",
        "capabilities": normalized,
        "legal_hold": body.legal_hold,
        "review": {
            "reviewed_by": actor.user_id,
            "review_note": body.review_note,
            "evidence_ids": body.evidence_ids,
        },
        "disclaimer": "This manifest records evidence and platform capabilities. It is not a copyright registration or legal opinion.",
    }
    manifest_hash = canonical_hash(manifest)
    manifest_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if body.evidence_ids:
            cur.execute(
                "UPDATE rights_evidence SET status='verified',reviewed_by=%s,reviewed_at=now() WHERE workspace_id=%s AND asset_snapshot_id=%s AND id=ANY(%s::uuid[]) AND status='submitted'",
                (actor.user_id, actor.workspace_id, asset_id, body.evidence_ids),
            )
        cur.execute(
            "INSERT INTO rights_manifests(id,workspace_id,asset_snapshot_id,version,status,provider,provider_approval,manifest,manifest_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (manifest_id, actor.workspace_id, asset_id, version, body.status, manifest["provider"], manifest["provider_approval"], psycopg2.extras.Json(manifest), manifest_hash),
        )
        row = cur.fetchone()
        audit(cur, actor, "rights.manifest.issue", "rights_manifest", manifest_id, {"asset_id": asset_id, "version": version, "legal_hold": body.legal_hold}, request.state.request_id)
    return serialize(row)


@router.get("/moderation/cases")
def list_cases(actor: Actor = Depends(require_roles("owner", "admin", "legal", "support"))):
    return serialize(fetch_all(
        "SELECT * FROM moderation_cases WHERE workspace_id=%s ORDER BY created_at DESC",
        (actor.workspace_id,),
        actor.workspace_id,
    ))


@router.post("/moderation/cases", status_code=201)
def create_case(body: CaseCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "legal", "support"))):
    case_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO moderation_cases(id,workspace_id,subject_type,subject_id,case_type,severity,evidence,legal_hold,opened_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (case_id, actor.workspace_id, body.subject_type, body.subject_id, body.case_type, body.severity, psycopg2.extras.Json(body.evidence), body.legal_hold, actor.user_id),
        )
        row = cur.fetchone()
        if body.legal_hold and body.subject_type == "project":
            cur.execute("UPDATE song_projects SET status='legal_hold',updated_at=now() WHERE id=%s AND workspace_id=%s", (body.subject_id, actor.workspace_id))
        audit(cur, actor, "moderation.case.open", "moderation_case", case_id, {"subject_type": body.subject_type, "subject_id": body.subject_id}, request.state.request_id)
    return serialize(row)


@router.put("/moderation/cases/{case_id}")
def update_case(case_id: str, body: CaseUpdate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "legal"))):
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE moderation_cases SET status=%s,restrictions=%s,legal_hold=%s,updated_at=now(),closed_at=CASE WHEN %s IN ('resolved','dismissed') THEN now() ELSE NULL END WHERE id=%s AND workspace_id=%s RETURNING *",
            (body.status, psycopg2.extras.Json(body.restrictions), body.legal_hold, body.status, case_id, actor.workspace_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "case not found")
        if row["subject_type"] == "project":
            next_status = "legal_hold" if body.legal_hold else "active"
            cur.execute("UPDATE song_projects SET status=%s,updated_at=now() WHERE id=%s AND workspace_id=%s", (next_status, row["subject_id"], actor.workspace_id))
        audit(cur, actor, "moderation.case.update", "moderation_case", case_id, {"status": body.status, "legal_hold": body.legal_hold}, request.state.request_id)
    return serialize(row)
