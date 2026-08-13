#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsS http://localhost:8000/openapi.json > "$tmp"
python - "$tmp" <<'PY'
import json, pathlib, sys, yaml
source=pathlib.Path(sys.argv[1])
data=json.loads(source.read_text())
root=pathlib.Path('shared/contracts')
(root/'openapi-v13.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(root/'openapi-v13.yaml').write_text(yaml.safe_dump(data,sort_keys=False,allow_unicode=True),encoding='utf-8')
print(f"wrote OpenAPI v{data['info']['version']} with {len(data.get('paths',{}))} paths")
PY
