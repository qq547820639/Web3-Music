-- v14 capacity/cost hardening. Additive indexes only; no domain semantics changed.

CREATE INDEX IF NOT EXISTS ix_jobs_ready_to_claim
  ON generation_jobs(next_attempt_at, created_at)
  WHERE status IN ('queued','retry_wait');

CREATE INDEX IF NOT EXISTS ix_jobs_expired_lease
  ON generation_jobs(lease_expires_at, created_at)
  WHERE status IN ('leased','submitting','provider_queued','processing','ingesting');

CREATE INDEX IF NOT EXISTS ix_jobs_cancel_requested
  ON generation_jobs(next_attempt_at, lease_expires_at, created_at)
  WHERE status='cancel_requested';

CREATE INDEX IF NOT EXISTS ix_jobs_workspace_created
  ON generation_jobs(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_provider_inbox_unprocessed
  ON provider_inbox(received_at)
  WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_generation_steps_job
  ON generation_steps(job_id, id);

CREATE INDEX IF NOT EXISTS ix_audio_candidates_job_ordinal
  ON audio_candidates(job_id, ordinal);

CREATE INDEX IF NOT EXISTS ix_credit_holds_workspace_created
  ON credit_holds(workspace_id, created_at DESC);
