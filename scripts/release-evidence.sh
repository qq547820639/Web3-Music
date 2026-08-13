#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="release-evidence/$stamp"
mkdir -p "$out"
{
  echo "release=v13.0.0-cost500"
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "python=$(python --version 2>&1)"
  echo "node=$(node --version 2>&1)"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || echo unavailable)"
} > "$out/environment.txt"
./scripts/static-verify.sh > "$out/static-verify.log" 2>&1
python - <<'PY' > "$out/compose-summary.json"
import json,yaml
x=yaml.safe_load(open('docker-compose.yml'))
print(json.dumps({'name':x.get('name'),'services':sorted(x['services']),'volumes':sorted(x.get('volumes',{}))},indent=2))
PY
find . -type f -not -path './release-evidence/*' -not -path './.git/*' -not -path '*/__pycache__/*' -not -path './.pytest_cache/*' -not -name '*.pyc' -print0 | sort -z | xargs -0 sha256sum > "$out/source.sha256"
python - <<'PY' > "$out/sbom-lite.json"
import json,pathlib,re
items=[]
for p in pathlib.Path('services').rglob('requirements.txt'):
    for line in p.read_text().splitlines():
        line=line.strip()
        if line and not line.startswith('#'):
            name,_,version=line.partition('==');items.append({'ecosystem':'pypi','name':name,'version':version or None,'source':str(p)})
for p in pathlib.Path('services').rglob('Dockerfile'):
    for line in p.read_text().splitlines():
        if line.upper().startswith('FROM '):items.append({'ecosystem':'container','name':line.split()[1],'source':str(p)})
print(json.dumps({'format':'resonance-sbom-lite-v1','components':items},indent=2))
PY
tar -czf "$out.tar.gz" -C "$(dirname "$out")" "$(basename "$out")"
sha256sum "$out.tar.gz" > "$out.tar.gz.sha256"
echo "$out.tar.gz"
