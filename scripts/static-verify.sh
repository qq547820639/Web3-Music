#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
find . -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
python -m compileall -q services tests scripts
find services -name '*.js' -print0 | xargs -0 -r -n1 node --check
find scripts -name '*.sh' -print0 | xargs -0 -r -n1 sh -n
# The tracked delivery manifest must describe the tree it ships beside. Regenerating
# it is a separate command on purpose, so this check can genuinely fail.
./scripts/source-manifest.sh check
# Same discipline for the authority matrix: docs/AUTHORITY_MATRIX.md and
# shared/contracts/authority-matrix.json are derived from the route decorators, and a gate that
# regenerated them here could never report a route whose role check disappeared.
python scripts/authority_matrix.py --check
python - <<'PY'
import json,pathlib,yaml
from jsonschema import Draft202012Validator
root=pathlib.Path('.')
for p in (root/'docker-compose.yml',root/'docker-compose.commercial-test.yml',root/'docker-compose.production.yml',root/'docker-compose.capacity500.yml'):
    value=yaml.safe_load(p.read_text(encoding='utf-8'))
    assert isinstance(value,dict) and 'services' in value,p
for p in root.rglob('*.json'):
    if not p.is_file():
        continue
    # Backup and drill output lands in ignored directories, and MinIO stores object metadata
    # in directories whose names end in .json — walking them made this gate crash, not fail.
    if any(part.startswith('.') or part in {'backups','release-evidence','capacity-results',
                                            'node_modules','__pycache__','.venv','venv'}
           for part in p.parts[:-1]):
        continue
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
# 读数格式自己的对照：解析与渲染必须能互相还原，否则下面这些 `metric` 行会一路静默到盖章那天。
python scripts/metric_line.py --self-test
# 阶梯的条数也写成机器读数。文书里"权威运行第 1 步读出 N 条"这一格此前只能手抄：数在逐步日志里，
# 而逐步日志被 .gitignore 挡在树外。`metric` 前缀由 run_step 抄进 SUMMARY.txt（见 scripts/metric_line.py）。
# 退码必须逐字保留，所以这里显式接管管道：pytest 失败要仍然把整步判红。
ladder_log=$(mktemp)
set +e
pytest -q tests/unit > "$ladder_log" 2>&1
ladder_rc=$?
set -e
cat "$ladder_log"
ladder_count=$(grep -o '[0-9][0-9]* passed' "$ladder_log" | tail -1 | awk '{print $1}')
rm -f "$ladder_log"
if [ -n "$ladder_count" ]; then
  printf 'metric unit_passed=%s\n' "$ladder_count"
fi
exit $ladder_rc
