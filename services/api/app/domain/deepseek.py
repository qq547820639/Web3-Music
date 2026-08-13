import json
import re
import httpx
from .patches import touches_locked


_client: httpx.AsyncClient | None = None

def shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client=httpx.AsyncClient(
            timeout=httpx.Timeout(45,connect=10),
            limits=httpx.Limits(max_connections=64,max_keepalive_connections=32),
        )
    return _client

async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client=None

SYSTEM_PROMPT = """
You are the SongPatch Orchestrator of an AI music asset platform.
Return JSON only. Never claim rights, charge credits, select a master, or invoke a music provider.
Propose only add/replace/remove JSON Patch operations against the supplied SongSpec.
Respect locked_paths and preserve any field requested by the user.
JSON shape:
{"reason":"...","operations":[{"op":"replace","path":"/styles","value":"..."}],"preserve":["/lyrics"]}
"""


def _mock_patch(spec: dict, message: str, locked_paths: list[str]):
    ops = []
    preserve = list(locked_paths)
    low = message.lower()
    if "保留歌词" in message or "不要改歌词" in message:
        preserve.append("/lyrics")
    if any(k in message for k in ["克制","更少","稀疏","简洁"]):
        current = str(spec.get("styles", ""))
        value = (current + ", restrained arrangement, sparse instrumentation, intimate vocal, controlled dynamics").strip(", ")
        ops.append({"op":"replace" if "styles" in spec else "add","path":"/styles","value":value})
    if any(k in message for k in ["更快","加快"]):
        ops.append({"op":"replace" if "bpm" in spec else "add","path":"/bpm","value":min(180, int(spec.get("bpm", 90))+10)})
    if any(k in message for k in ["更慢","慢一点"]):
        ops.append({"op":"replace" if "bpm" in spec else "add","path":"/bpm","value":max(45, int(spec.get("bpm", 90))-10)})
    if "副歌" in message and "突出" in message:
        hook = spec.get("hook") or "再一次"
        ops.append({"op":"replace" if "hook" in spec else "add","path":"/hook","value":hook})
        current = str(spec.get("styles", ""))
        ops.append({"op":"replace" if "styles" in spec else "add","path":"/styles","value":(current + ", chorus lift, memorable hook, wider final chorus").strip(", ")})
    if not ops:
        current = str(spec.get("styles", ""))
        ops.append({"op":"replace" if "styles" in spec else "add","path":"/styles","value":(current + ", refined emotional arc, clear vocal, production-ready structure").strip(", ")})
    filtered = [op for op in ops if not any(touches_locked(op["path"], [p]) for p in preserve)]
    return {"reason":"deterministic local patch proposer", "operations":filtered, "preserve":sorted(set(preserve)), "source":"mock"}


async def propose_patch(spec: dict, message: str, locked_paths: list[str], api_key: str, base_url: str, model: str):
    if not api_key:
        return _mock_patch(spec, message, locked_paths)
    payload = {
        "model": model,
        "messages": [
            {"role":"system","content":SYSTEM_PROMPT},
            {"role":"user","content":"Return JSON.\nSongSpec:\n" + json.dumps(spec, ensure_ascii=False) + "\nlocked_paths:" + json.dumps(locked_paths) + "\nUser request:" + message},
        ],
        "response_format": {"type":"json_object"},
        "max_tokens": 1800,
        "stream": False,
    }
    last_error = None
    client=shared_client()
    for _ in range(2):
        try:
            response = await client.post(base_url.rstrip("/") + "/chat/completions", headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"].get("content")
            if not content:
                raise ValueError("DeepSeek returned empty content")
            data = json.loads(content)
            operations = data.get("operations") or []
            if not isinstance(operations, list):
                raise ValueError("operations must be an array")
            for op in operations:
                if op.get("op") not in {"add","replace","remove"} or not isinstance(op.get("path"), str):
                    raise ValueError("invalid patch operation")
                if touches_locked(op["path"], locked_paths):
                    raise ValueError(f"model attempted locked path {op['path']}")
            data["source"] = "deepseek"
            return data
        except Exception as exc:
            last_error = exc
    fallback = _mock_patch(spec, message, locked_paths)
    fallback["warning"] = f"DeepSeek failed; fallback used: {last_error}"
    return fallback
