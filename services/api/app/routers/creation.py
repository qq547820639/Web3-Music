from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import Actor, require_roles
from ..common import audit, serialize
from ..db import fetch_all, fetch_one, transaction

router = APIRouter(prefix="/api", tags=["Creation OS"])


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_revision: int | None = None


class BranchAdvance(BaseModel):
    head_revision: int


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    spec_revision: int | None = None
    candidate_id: str | None = None
    timecode_ms: int | None = Field(default=None, ge=0)


class PreferenceUpdate(BaseModel):
    scope: str = Field(pattern="^(global|workspace|project)$")
    project_id: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    learning_enabled: bool = True


class ProductEventCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=120)
    project_id: str | None = None
    asset_snapshot_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


def _project(project_id: str, actor: Actor):
    row = fetch_one(
        "SELECT id,current_revision,status FROM song_projects WHERE id=%s AND workspace_id=%s",
        (project_id, actor.workspace_id),
        actor.workspace_id,
    )
    if not row:
        raise HTTPException(404, "project not found")
    return row


@router.get("/projects/{project_id}/branches")
def list_branches(project_id: str, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer"))):
    project = _project(project_id, actor)
    rows = fetch_all(
        "SELECT * FROM project_branches WHERE project_id=%s AND workspace_id=%s ORDER BY created_at",
        (project_id, actor.workspace_id),
        actor.workspace_id,
    )
    if not rows:
        return [{
            "id": None,
            "name": "main",
            "head_revision": project["current_revision"],
            "base_revision": 1,
            "virtual": True,
        }]
    return serialize(rows)


@router.post("/projects/{project_id}/branches", status_code=201)
def create_branch(project_id: str, body: BranchCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator"))):
    project = _project(project_id, actor)
    revision = body.base_revision or int(project["current_revision"])
    exists = fetch_one(
        "SELECT revision FROM song_spec_revisions WHERE workspace_id=%s AND project_id=%s AND revision=%s",
        (actor.workspace_id, project_id, revision),
        actor.workspace_id,
    )
    if not exists:
        raise HTTPException(404, "base revision not found")
    branch_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(
                "INSERT INTO project_branches(id,workspace_id,project_id,name,head_revision,base_revision,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (branch_id, actor.workspace_id, project_id, body.name.strip(), revision, revision, actor.user_id),
            )
            row = cur.fetchone()
        except Exception as exc:
            if "unique" in str(exc).lower():
                raise HTTPException(409, "branch name already exists")
            raise
        audit(cur, actor, "branch.create", "project_branch", branch_id, {"project_id": project_id, "base_revision": revision}, request.state.request_id)
    return serialize(row)


@router.put("/projects/{project_id}/branches/{branch_id}")
def advance_branch(project_id: str, branch_id: str, body: BranchAdvance, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator"))):
    _project(project_id, actor)
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT 1 FROM song_spec_revisions WHERE workspace_id=%s AND project_id=%s AND revision=%s",
            (actor.workspace_id, project_id, body.head_revision),
        )
        if not cur.fetchone():
            raise HTTPException(404, "revision not found")
        cur.execute(
            "UPDATE project_branches SET head_revision=%s,updated_at=now() WHERE id=%s AND project_id=%s AND workspace_id=%s RETURNING *",
            (body.head_revision, branch_id, project_id, actor.workspace_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "branch not found")
        audit(cur, actor, "branch.advance", "project_branch", branch_id, {"head_revision": body.head_revision}, request.state.request_id)
    return serialize(row)


@router.get("/projects/{project_id}/comments")
def list_comments(project_id: str, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer"))):
    _project(project_id, actor)
    return serialize(fetch_all(
        "SELECT c.*,u.display_name AS author_name FROM project_comments c JOIN users u ON u.id=c.created_by WHERE c.project_id=%s AND c.workspace_id=%s ORDER BY c.created_at",
        (project_id, actor.workspace_id),
        actor.workspace_id,
    ))


@router.post("/projects/{project_id}/comments", status_code=201)
def create_comment(project_id: str, body: CommentCreate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer"))):
    _project(project_id, actor)
    if body.candidate_id:
        candidate = fetch_one(
            "SELECT id FROM audio_candidates c JOIN generation_jobs j ON j.id=c.job_id WHERE c.id=%s AND c.workspace_id=%s AND j.project_id=%s",
            (body.candidate_id, actor.workspace_id, project_id),
            actor.workspace_id,
        )
        if not candidate:
            raise HTTPException(404, "candidate not found in project")
    comment_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO project_comments(id,workspace_id,project_id,spec_revision,candidate_id,timecode_ms,body,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (comment_id, actor.workspace_id, project_id, body.spec_revision, body.candidate_id, body.timecode_ms, body.body.strip(), actor.user_id),
        )
        row = cur.fetchone()
        audit(cur, actor, "comment.create", "project_comment", comment_id, {"project_id": project_id, "candidate_id": body.candidate_id}, request.state.request_id)
    return serialize(row)


@router.post("/projects/{project_id}/comments/{comment_id}/resolve")
def resolve_comment(project_id: str, comment_id: str, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer"))):
    _project(project_id, actor)
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE project_comments SET status='resolved',resolved_by=%s,resolved_at=now() WHERE id=%s AND project_id=%s AND workspace_id=%s AND status='open' RETURNING *",
            (actor.user_id, comment_id, project_id, actor.workspace_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "open comment not found")
        audit(cur, actor, "comment.resolve", "project_comment", comment_id, {}, request.state.request_id)
    return serialize(row)


@router.get("/preferences")
def get_preferences(actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal", "support"))):
    return serialize(fetch_all(
        "SELECT * FROM user_preferences WHERE workspace_id=%s AND user_id=%s ORDER BY scope,updated_at DESC",
        (actor.workspace_id, actor.user_id),
        actor.workspace_id,
    ))


@router.put("/preferences")
def update_preferences(body: PreferenceUpdate, request: Request, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal", "support"))):
    if body.scope == "project" and not body.project_id:
        raise HTTPException(400, "project_id is required for project scope")
    if body.scope != "project" and body.project_id:
        raise HTTPException(400, "project_id is only allowed for project scope")
    if body.project_id:
        _project(body.project_id, actor)
    with transaction(actor.workspace_id) as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM user_preferences WHERE workspace_id=%s AND user_id=%s AND scope=%s AND project_id IS NOT DISTINCT FROM %s FOR UPDATE",
            (actor.workspace_id, actor.user_id, body.scope, body.project_id),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE user_preferences SET preferences=%s,learning_enabled=%s,updated_at=now() WHERE id=%s RETURNING *",
                (psycopg2.extras.Json(body.preferences), body.learning_enabled, existing["id"]),
            )
        else:
            cur.execute(
                "INSERT INTO user_preferences(workspace_id,user_id,scope,project_id,preferences,learning_enabled) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                (actor.workspace_id, actor.user_id, body.scope, body.project_id, psycopg2.extras.Json(body.preferences), body.learning_enabled),
            )
        row = cur.fetchone()
        audit(cur, actor, "preferences.update", "user_preferences", str(row["id"]), {"scope": body.scope, "learning_enabled": body.learning_enabled}, request.state.request_id)
    return serialize(row)


@router.post("/events", status_code=202)
def capture_event(body: ProductEventCreate, actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "viewer", "billing", "legal", "support"))):
    allowed = {
        "candidate_played", "candidate_completed", "ab_choice", "master_selected", "asset_downloaded",
        "generation_feedback", "quality_viewed", "license_viewed", "license_purchased", "project_shared",
    }
    if body.event_name not in allowed:
        raise HTTPException(400, "unsupported event")
    if body.project_id:
        _project(body.project_id, actor)
    event_id = str(uuid.uuid4())
    with transaction(actor.workspace_id) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO product_events(id,workspace_id,user_id,project_id,asset_snapshot_id,event_name,properties) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (event_id, actor.workspace_id, actor.user_id, body.project_id, body.asset_snapshot_id, body.event_name, psycopg2.extras.Json(body.properties)),
        )
    return {"accepted": True, "event_id": event_id}


@router.get("/analytics/overview")
def analytics_overview(actor: Actor = Depends(require_roles("owner", "admin", "creator", "reviewer", "billing"))):
    since = datetime.now(timezone.utc) - timedelta(days=28)
    metrics = fetch_one(
        """
        SELECT
          (SELECT count(*) FROM generation_jobs WHERE workspace_id=%s AND created_at>=%s) AS generations,
          (SELECT count(*) FROM generation_jobs WHERE workspace_id=%s AND status IN ('completed','partial') AND created_at>=%s) AS successful_jobs,
          (SELECT count(*) FROM master_selections WHERE workspace_id=%s AND created_at>=%s) AS masters,
          (SELECT count(*) FROM asset_snapshots WHERE workspace_id=%s AND created_at>=%s) AS assets,
          (SELECT count(*) FROM licenses WHERE (seller_workspace_id=%s OR buyer_workspace_id=%s) AND created_at>=%s) AS licenses,
          (SELECT COALESCE(avg(score),0) FROM quality_evaluations WHERE workspace_id=%s AND created_at>=%s) AS avg_quality
        """,
        (actor.workspace_id, since, actor.workspace_id, since, actor.workspace_id, since, actor.workspace_id, since, actor.workspace_id, actor.workspace_id, since, actor.workspace_id, since),
        actor.workspace_id,
    )
    events = fetch_all(
        "SELECT event_name,count(*) AS count FROM product_events WHERE workspace_id=%s AND occurred_at>=%s GROUP BY event_name ORDER BY count(*) DESC",
        (actor.workspace_id, since),
        actor.workspace_id,
    )
    generations = int(metrics["generations"] or 0)
    masters = int(metrics["masters"] or 0)
    return serialize({
        "window_days": 28,
        "metrics": {
            **metrics,
            "master_conversion": round(masters / generations, 4) if generations else 0,
        },
        "events": events,
    })
