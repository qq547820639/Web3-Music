import json, os
from pathlib import Path
from jsonschema import Draft202012Validator
from fastapi import HTTPException

_candidates=[Path(os.getenv("CONTRACTS_DIR","/app/contracts"))/"song-spec-runtime-v1.schema.json",Path(__file__).resolve().parents[3]/"shared/contracts/song-spec-runtime-v1.schema.json"]
_schema_path=next((p for p in _candidates if p.exists()),None)
if not _schema_path: raise RuntimeError("SongSpec runtime schema is missing")
_validator=Draft202012Validator(json.loads(_schema_path.read_text(encoding="utf-8")))

def validate_song_spec(spec:dict):
    errors=sorted(_validator.iter_errors(spec),key=lambda e:list(e.path))
    if errors:
        items=[{"path":"/"+"/".join(map(str,e.path)),"message":e.message} for e in errors[:20]]
        raise HTTPException(422,{"code":"song_spec_validation_failed","errors":items})
    return spec
