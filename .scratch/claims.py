import pathlib, re, subprocess

L = pathlib.Path("docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8").splitlines()
for n, needle in ((17, "十三"), (24, "56/56"), (24, "229"), (44, "1286"), (46, "只有")):
    t = L[n - 1]
    i = t.find(needle)
    window = t[max(0, i - 200):i + 200] if i >= 0 else "NOT ON THIS LINE"
    print("[%d][%s] %s\n" % (n, needle, window))

T = pathlib.Path("docs/TEST_REPORT.md").read_text(encoding="utf-8").splitlines()
i = T[133].find("16 条")
print("[TR134] %s" % T[133][max(0, i - 160):i + 160])

files = subprocess.run(["git", "ls-files", "release-evidence/acceptance-*/SUMMARY.txt"],
                       capture_output=True, text=True).stdout.split()
prov = gen = 0
for f in files:
    s = open(f, encoding="utf-8").read()
    if re.search(r"^provider-regression-100 \| PASS", s, re.M):
        prov += 1
    if re.search(r"^generic-rest-roundtrip \| PASS", s, re.M):
        gen += 1
print("tracked SUMMARYs=%d provider-regression PASS rows=%d generic PASS rows=%d" % (len(files), prov, gen))

for needle in ("56/56 checks", "Across all eleven green runs", "210 unit tests", "96 view records"):
    out = subprocess.run(["git", "log", "-S", needle, "--format=%h %ad", "--date=short", "--",
                          "docs/FINAL_RELEASE_STATUS.md"], capture_output=True, text=True).stdout.strip()
    first = out.splitlines()[-1] if out else "not found"
    print("first commit touching %r: %s" % (needle, first))
