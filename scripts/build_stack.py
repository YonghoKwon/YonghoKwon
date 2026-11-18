#!/usr/bin/env python3
"""Build assets/stack-{light,dark}.svg from the owner's public GitHub repositories.

Usage: GITHUB_TOKEN=... python3 scripts/build_stack.py <owner> [--offline data.json]
Rules live in scripts/stack.json. If the GitHub API fails, the existing SVGs are kept.
"""
import base64, fnmatch, json, os, pathlib, sys, urllib.error, urllib.request
from xml.sax.saxutils import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://api.github.com"
MAX_FILE_FETCH = 15  # per repo, for file_contains rules


def api(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "profile-stack-card"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def match_glob(paths, pattern):
    return [p for p in paths if fnmatch.fnmatchcase(p, pattern) or fnmatch.fnmatchcase(p.lower(), pattern.lower())]


def collect(owner, cfg):
    """Return list of repos: {name, languages{lang:bytes}, topics[], paths[], read(path)->str}."""
    repos, page = [], 1
    while True:
        batch = api(f"/users/{owner}/repos?type=owner&per_page=100&page={page}")
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    out = []
    for r in repos:
        if r.get("fork") or r.get("archived") or r.get("private") or r["name"].lower() == owner.lower():
            continue
        full = r["full_name"]
        langs = api(f"/repos/{full}/languages")
        try:
            tree = api(f"/repos/{full}/git/trees/{r['default_branch']}?recursive=1").get("tree", [])
        except urllib.error.HTTPError:
            tree = []
        paths = [t["path"] for t in tree if t.get("type") == "blob"]

        def read(path, full=full):
            try:
                c = api(f"/repos/{full}/contents/{urllib.request.quote(path)}")
                return base64.b64decode(c.get("content", "")).decode("utf-8", "replace")
            except Exception:
                return ""

        out.append({"name": r["name"], "languages": langs, "topics": r.get("topics", []), "paths": paths, "read": read})
    return out


def detect(repos, cfg):
    total = {}
    for r in repos:
        for k, v in r["languages"].items():
            total[k] = total.get(k, 0) + v
    all_bytes = sum(total.values()) or 1
    share = {k: v / all_bytes for k, v in total.items()}
    topics = {t for r in repos for t in r["topics"]}
    cache = {}

    def file_contains(rule):
        for r in repos:
            for p in match_glob(r["paths"], rule["glob"])[:MAX_FILE_FETCH]:
                key = (r["name"], p)
                if key not in cache:
                    cache[key] = r["read"](p)
                if rule["text"].lower() in cache[key].lower():
                    return True
        return False

    def hit(item):
        if item.get("always"):
            return True
        if any(share.get(l, 0) >= cfg["min_share"] for l in item.get("languages", [])):
            return True
        if topics & set(item.get("topics", [])):
            return True
        for r in repos:
            if any(match_glob(r["paths"], g) for g in item.get("files", [])):
                return True
            if any(s in p.lower() for s in item.get("path_contains", []) for p in r["paths"]):
                return True
        return any(file_contains(fc) for fc in item.get("file_contains", []))

    groups, mapped = [], set()
    for g in cfg["groups"]:
        for it in g["items"]:
            mapped.update(it.get("languages", []))
        hidden = set(cfg.get("hide", []))
        chips = [it["name"] for it in g["items"] if it["name"] not in hidden and hit(it)]
        if chips:
            groups.append((g["label"], chips))
    other = [l for l, s in sorted(share.items(), key=lambda x: -x[1])
             if l not in mapped and l not in cfg["ignore_languages"] and l not in cfg.get("hide", []) and s >= cfg["other_min_share"]]
    if other:
        groups.append((cfg["other_label"], other))
    return groups


THEMES = {
    "light": dict(bg="#f6f5f1", fg="#1b1b1a", muted="#6f6d68", line="#d8d5cd", acc="#d9480f", chip="#ffffff"),
    "dark": dict(bg="#16171b", fg="#ecebe7", muted="#93918b", line="#2e2f35", acc="#ff7a45", chip="#1d1e23"),
}
CW, FS, PAD, GAP, LINEH, ROWGAP, TOP, X0, XMAX = 9.0, 15, 16, 10, 46, 6, 96, 240, 944


def layout(groups):
    rows, y = [], TOP
    for label, chips in groups:
        x, placed = X0, []
        for c in chips:
            w = round(len(c) * CW + PAD * 2)
            if x + w > XMAX and placed:
                x, y = X0, y + LINEH
            placed.append((c, x, y, w))
            x += w + GAP
        rows.append((label, placed[0][2], placed))
        y += LINEH + ROWGAP
    return rows, y - ROWGAP + 28


def render(groups, t):
    rows, h = layout(groups)
    body = []
    for label, ly, chips in rows:
        body.append(f'  <text class="lbl" x="56" y="{ly + 22}">{escape(label)}</text>')
        for c, x, y, w in chips:
            body.append(f'  <rect x="{x}" y="{y}" width="{w}" height="34" rx="17" fill="{t["chip"]}" stroke="{t["line"]}"/>')
            body.append(f'  <text class="chip" x="{x + w / 2:.1f}" y="{y + 22}" text-anchor="middle">{escape(c)}</text>')
    desc = escape("; ".join(f"{l}: {', '.join(c)}" for l, c in groups))
    mono = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{h}" viewBox="0 0 1000 {h}" role="img" aria-labelledby="t d">
  <title id="t">Stack</title>
  <desc id="d">{desc}</desc>
  <style>
    .head {{ font-family: {mono}; font-size: 13px; letter-spacing: 1.5px; fill: {t['muted']}; }}
    .lbl {{ font-family: {mono}; font-size: 13px; letter-spacing: 1.5px; fill: {t['acc']}; }}
    .chip {{ font-family: {mono}; font-size: {FS}px; fill: {t['fg']}; }}
  </style>
  <rect width="1000" height="{h}" rx="6" fill="{t['bg']}"/>
  <text class="head" x="56" y="58">STACK</text>
  <line x1="130" y1="53" x2="944" y2="53" stroke="{t['line']}"/>
''' + "\n".join(body) + "\n</svg>\n"


def main():
    args = sys.argv[1:]
    owner = args[0]
    cfg = json.loads((ROOT / "scripts/stack.json").read_text())
    try:
        if "--offline" in args:  # test data: [{name, languages, topics, paths, files:{path:text}}]
            data = json.loads(pathlib.Path(args[args.index("--offline") + 1]).read_text())
            repos = [dict(d, read=lambda p, f=d.get("files", {}): f.get(p, "")) for d in data]
        else:
            repos = collect(owner, cfg)
    except Exception as e:  # keep the previous card rather than publishing a broken one
        print(f"::warning::stack card not updated: {e}")
        return 0
    if not repos:
        print("::warning::no public repositories found; stack card not updated")
        return 0
    groups = detect(repos, cfg)
    for name, t in THEMES.items():
        (ROOT / f"assets/stack-{name}.svg").write_text(render(groups, t))
    for label, chips in groups:
        print(f"{label:14} {', '.join(chips)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
