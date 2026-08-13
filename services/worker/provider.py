from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx



_http_client: httpx.AsyncClient | None = None

def shared_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        max_connections=max(20,int(os.getenv("PROVIDER_HTTP_MAX_CONNECTIONS","64")))
        _http_client=httpx.AsyncClient(
            limits=httpx.Limits(max_connections=max_connections,max_keepalive_connections=max_connections),
            timeout=httpx.Timeout(45,connect=10),
        )
    return _http_client

async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client=None

@dataclass
class ProviderResult:
    status: str
    provider_job_id: str
    candidates: list[dict[str, Any]]
    raw: dict[str, Any]


class ProviderAdapter:
    async def get_capabilities(self) -> dict:
        raise NotImplementedError

    async def quote(self, spec: dict, count: int) -> dict:
        return {"candidate_count": count, "estimated_cost": count * 0.1}

    async def submit(self, job_id: str, spec: dict, count: int, scenario: str) -> str:
        raise NotImplementedError

    async def get_status(self, provider_job_id: str) -> ProviderResult:
        raise NotImplementedError

    async def cancel(self, provider_job_id: str) -> bool:
        return False

    async def health_check(self) -> dict:
        return {"status": "unknown"}

    def reconcile_cost(self, result: ProviderResult, ready_count: int) -> float | None:
        return None


class EmulatorAdapter(ProviderAdapter):
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_capabilities(self):
        response=await shared_http_client().get(self.base_url + "/v1/capabilities",timeout=10)
        response.raise_for_status()
        return response.json()

    async def submit(self, job_id, spec, count, scenario):
        payload = {
            "title": spec.get("title", "Untitled"),
            "lyrics": spec.get("lyrics", ""),
            "styles": spec.get("styles", ""),
            "bpm": int(spec.get("bpm", 90)),
            "candidate_count": count,
            "scenario": scenario,
        }
        response = await shared_http_client().post(
            self.base_url + "/v1/jobs", json=payload, headers={"Idempotency-Key": job_id},timeout=30
        )
        response.raise_for_status()
        return response.json()["id"]

    async def get_status(self, provider_job_id):
        response = await shared_http_client().get(self.base_url + f"/v1/jobs/{provider_job_id}",timeout=20)
        response.raise_for_status()
        data = response.json()
        return ProviderResult(data.get("status", "processing"), provider_job_id, data.get("results", []), data)

    async def cancel(self, provider_job_id):
        response = await shared_http_client().post(self.base_url + f"/v1/jobs/{provider_job_id}/cancel",timeout=10)
        return response.status_code < 300

    async def health_check(self):
        response=await shared_http_client().get(self.base_url + "/health",timeout=10)
        response.raise_for_status()
        return response.json()

    def reconcile_cost(self, result: ProviderResult, ready_count: int) -> float:
        return round(ready_count * 0.1, 6)


class GenericRESTAdapter(ProviderAdapter):
    """Contract-first adapter for an approved asynchronous music provider.

    The external service must expose a submit endpoint and a status endpoint.
    Paths and authentication are environment-driven so the core product never
    embeds a vendor's private or unstable account protocol.
    """

    TERMINAL_MAP = {
        "succeeded": "completed",
        "success": "completed",
        "completed": "completed",
        "partial_success": "partial",
        "partial": "partial",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "queued": "processing",
        "pending": "processing",
        "running": "processing",
        "processing": "processing",
    }

    def __init__(self):
        base_url = os.getenv("GENERIC_PROVIDER_BASE_URL", "").strip()
        if not base_url:
            raise RuntimeError("GENERIC_PROVIDER_BASE_URL is required")
        self.base_url = base_url.rstrip("/")
        self.submit_path = os.getenv("GENERIC_PROVIDER_SUBMIT_PATH", "/v1/jobs")
        self.status_path = os.getenv("GENERIC_PROVIDER_STATUS_PATH", "/v1/jobs/{job_id}")
        self.cancel_path = os.getenv("GENERIC_PROVIDER_CANCEL_PATH", "/v1/jobs/{job_id}/cancel")
        self.capabilities_path = os.getenv("GENERIC_PROVIDER_CAPABILITIES_PATH", "/v1/capabilities")
        self.health_path = os.getenv("GENERIC_PROVIDER_HEALTH_PATH", "/health")
        self.api_key = os.getenv("GENERIC_PROVIDER_API_KEY", "")
        self.auth_header = os.getenv("GENERIC_PROVIDER_AUTH_HEADER", "Authorization")
        self.auth_prefix = os.getenv("GENERIC_PROVIDER_AUTH_PREFIX", "Bearer ")
        self.model = os.getenv("GENERIC_PROVIDER_MODEL", "")

    def _headers(self, operation_key: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers[self.auth_header] = self.auth_prefix + self.api_key
        if operation_key:
            headers["Idempotency-Key"] = operation_key
        return headers

    @staticmethod
    def _job_id(data: dict[str, Any]) -> str:
        candidates = [
            data.get("id"),
            data.get("job_id"),
            data.get("task_id"),
            (data.get("data") or {}).get("id") if isinstance(data.get("data"), dict) else None,
            (data.get("data") or {}).get("job_id") if isinstance(data.get("data"), dict) else None,
        ]
        result = next((str(value) for value in candidates if value), "")
        if not result:
            raise RuntimeError("generic provider response has no job identifier")
        return result

    @staticmethod
    def _candidate_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
        rows = data.get("candidates") or data.get("results") or data.get("clips") or []
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("data") or []
        normalized = []
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            audio_url = row.get("audio_url") or row.get("audioUrl") or row.get("url")
            status = str(row.get("status") or ("completed" if audio_url else "processing")).lower()
            normalized.append(
                {
                    "id": str(row.get("id") or row.get("clip_id") or row.get("song_id") or index),
                    "status": status,
                    "audio_url": audio_url,
                    "title": row.get("title"),
                    "duration": row.get("duration") or row.get("duration_seconds"),
                    "metadata": {"provider_payload": row},
                }
            )
        return normalized

    async def get_capabilities(self) -> dict:
        response = await shared_http_client().get(self.base_url + self.capabilities_path, headers=self._headers(),timeout=15)
        response.raise_for_status()
        return response.json()

    async def quote(self, spec: dict, count: int) -> dict:
        return {
            "candidate_count": count,
            "estimated_cost": None,
            "model": self.model or None,
            "provider": "generic_rest",
        }

    async def submit(self, job_id: str, spec: dict, count: int, scenario: str) -> str:
        payload = {
            "external_request_id": job_id,
            "model": self.model or None,
            "candidate_count": count,
            "song_spec": spec,
        }
        response = await shared_http_client().post(
            self.base_url + self.submit_path,
            json=payload,
            headers=self._headers(job_id),
            timeout=90,
        )
        response.raise_for_status()
        return self._job_id(response.json())

    async def get_status(self, provider_job_id: str) -> ProviderResult:
        path = self.status_path.format(job_id=provider_job_id)
        response = await shared_http_client().get(self.base_url + path, headers=self._headers(),timeout=45)
        response.raise_for_status()
        data = response.json()
        raw_status = str(data.get("status") or (data.get("data") or {}).get("status") or "processing").lower()
        status = self.TERMINAL_MAP.get(raw_status, "processing")
        return ProviderResult(status, provider_job_id, self._candidate_rows(data), data)

    async def cancel(self, provider_job_id: str) -> bool:
        if not self.cancel_path:
            return False
        path = self.cancel_path.format(job_id=provider_job_id)
        response = await shared_http_client().post(self.base_url + path, headers=self._headers(provider_job_id + ":cancel"),timeout=20)
        return response.status_code < 300

    async def health_check(self) -> dict:
        response = await shared_http_client().get(self.base_url + self.health_path, headers=self._headers(),timeout=10)
        response.raise_for_status()
        return response.json()

    def reconcile_cost(self, result: ProviderResult, ready_count: int) -> float | None:
        for key in ("cost", "actual_cost", "provider_cost"):
            value = result.raw.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None


class SunoCommunityAdapter(ProviderAdapter):
    """Development-only community bridge. Never grants commercial capability."""

    def __init__(self, base_url: str, model: str):
        if not base_url:
            raise RuntimeError("SUNO_API_BASE_URL is required")
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _collect_ids(self, value, out=None):
        out = out or []
        if isinstance(value, list):
            for item in value:
                self._collect_ids(item, out)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"id", "clip_id", "clipId", "song_id", "songId"} and isinstance(item, str):
                    out.append(item)
                elif isinstance(item, (dict, list)):
                    self._collect_ids(item, out)
        return list(dict.fromkeys(out))

    def _tracks(self, value):
        tracks = []

        def visit(item):
            if isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                if any(key in item for key in ("audio_url", "audioUrl", "clip_id", "status")):
                    tracks.append(item)
                for child in item.values():
                    if isinstance(child, (dict, list)):
                        visit(child)

        visit(value)
        output = []
        for track in tracks:
            output.append(
                {
                    "id": track.get("id") or track.get("clip_id") or track.get("clipId"),
                    "status": track.get("status") or ("completed" if track.get("audio_url") or track.get("audioUrl") else "processing"),
                    "audio_url": track.get("audio_url") or track.get("audioUrl"),
                    "title": track.get("title"),
                    "duration": track.get("duration"),
                    "metadata": {"raw": track, "provider": "suno_community"},
                }
            )
        return list({(item.get("id") or item.get("audio_url")): item for item in output if item.get("id") or item.get("audio_url")}.values())

    async def get_capabilities(self):
        return {"provider": "suno_community", "approval_status": "development_only", "supports_cancel": False, "supports_webhooks": False}

    async def submit(self, job_id, spec, count, scenario):
        payload = {
            "prompt": spec.get("lyrics", ""),
            "mv": self.model,
            "title": spec.get("title", "Untitled"),
            "tags": spec.get("styles", ""),
            "negative_tags": "",
        }
        response = await shared_http_client().post(self.base_url + "/generate", json=payload, headers={"Idempotency-Key": job_id},timeout=90)
        response.raise_for_status()
        data = response.json()
        ids = self._collect_ids(data)
        if not ids:
            ids = [item["id"] for item in self._tracks(data) if item.get("id")]
        if not ids:
            raise RuntimeError("community bridge returned no clip ids")
        return ",".join(ids)

    async def get_status(self, provider_job_id):
        ids = [item for item in provider_job_id.split(",") if item]
        response = await shared_http_client().get(self.base_url + "/feed/" + ",".join(ids),timeout=40)
        response.raise_for_status()
        data = response.json()
        tracks = self._tracks(data)
        completed = [item for item in tracks if item.get("audio_url")]
        failed = [item for item in tracks if str(item.get("status", "")).lower() in {"failed", "error"}]
        status = (
            "completed"
            if len(completed) >= len(ids)
            else "partial"
            if completed and len(completed) + len(failed) >= len(ids)
            else "failed"
            if failed and not completed and len(failed) >= len(ids)
            else "processing"
        )
        return ProviderResult(status, provider_job_id, tracks, data)

    async def health_check(self):
        return {"status": "unknown", "provider": "suno_community"}


@lru_cache(maxsize=8)
def create_adapter(provider: str | None = None):
    provider = provider or os.getenv("MUSIC_PROVIDER", "emulator")
    if provider == "generic_rest":
        return GenericRESTAdapter(), provider
    if provider == "suno_community":
        return SunoCommunityAdapter(os.getenv("SUNO_API_BASE_URL", ""), os.getenv("SUNO_MODEL", "chirp-v3-0")), provider
    if provider == "synthetic_licensed":
        return EmulatorAdapter(os.getenv("PROVIDER_BASE_URL", "http://provider-emulator:8010")), "synthetic_licensed"
    return EmulatorAdapter(os.getenv("PROVIDER_BASE_URL", "http://provider-emulator:8010")), "emulator"
