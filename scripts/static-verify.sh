#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
find . -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
python -m compileall -q services tests scripts
find services -name '*.js' -print0 | xargs -0 -r -n1 node --check
find scripts -name '*.sh' -print0 | xargs -0 -r -n1 sh -n
python - <<'PY'
import json,pathlib,yaml
from jsonschema import Draft202012Validator
root=pathlib.Path('.')
for p in (root/'docker-compose.yml',root/'docker-compose.commercial-test.yml',root/'docker-compose.production.yml',root/'docker-compose.capacity500.yml'):
    value=yaml.safe_load(p.read_text(encoding='utf-8'))
    assert isinstance(value,dict) and 'services' in value,p
for p in root.rglob('*.json'):
    json.loads(p.read_text(encoding='utf-8'))
for p in (root/'shared/contracts').rglob('*.schema.json'):
    Draft202012Validator.check_schema(json.loads(p.read_text(encoding='utf-8')))
j=json.loads((root/'shared/contracts/openapi-v13.json').read_text(encoding='utf-8'))
y=yaml.safe_load((root/'shared/contracts/openapi-v13.yaml').read_text(encoding='utf-8'))
assert j==y,'OpenAPI JSON and YAML differ'

for p in (root/'infrastructure/kubernetes').glob('*.yaml'):
    docs=list(yaml.safe_load_all(p.read_text(encoding='utf-8')))
    assert all(doc is None or isinstance(doc,dict) for doc in docs),p
print('Compose, Kubernetes YAML, JSON, JSON Schema and OpenAPI contracts valid')
PY
python scripts/architecture-audit.py
pytest -q tests/unit
