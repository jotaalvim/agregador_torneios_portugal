"""Compare what the sources have against what the site already has.

Answers the question the import runs never could: *is anything missing, and
how much?* Feed it the JSON files written by `scrape_chess_results.py scrape`
and `fetch_sources.py`, and it prints the tournaments that are not on the site
yet, plus a coverage figure per source.

    python3 scripts/fetch_sources.py --out /tmp/sources.json
    python3 scripts/scrape_chess_results.py scrape --out /tmp/cr.json
    python3 scripts/gaps.py /tmp/cr.json /tmp/sources.json

    # write just the missing ones, ready to review and submit
    python3 scripts/gaps.py /tmp/*.json --out /tmp/missing.json

Site state comes from the admin dashboard when `.env` has credentials, so
tournaments still sitting in the pending queue count as covered and don't get
proposed again. Without credentials it falls back to the public `.ics` feed,
which only lists approved ones — then anything pending looks missing.

Matching is by normalized title plus date, with a same-date/similar-title
fallback. It is deliberately loose: a false "already there" is worse than a
false "missing", because the first silently drops a tournament and the second
only costs you a look.

Standard library only, so it runs without the app's virtualenv.
"""

import argparse
import difflib
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SITE = "https://torneiosdexadrez.pt"
SIMILARITY = 0.80


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").lower())
    return "".join(c for c in value if c.isalnum())


def significant(value: str) -> set[str]:
    """Content words of a title, for the similarity fallback."""
    stop = {"de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "em",
            "por", "no", "na", "torneio", "open", "campeonato", "cd", "cn"}
    words = re.findall(r"\w+", unicodedata.normalize("NFKD", (value or "").lower()))
    words = ["".join(c for c in w if c.isalnum()) for w in words]
    return {w for w in words if w and w not in stop and not w.isdigit()}


def site_from_ics(site: str) -> list[dict]:
    from scrape_chess_results import fetch

    ics = fetch(site.rstrip("/") + "/calendario.ics").replace("\r\n ", "")
    out = []
    for event in ics.split("BEGIN:VEVENT")[1:]:
        start = re.search(r"DTSTART;VALUE=DATE:(\d{8})", event)
        summary = re.search(r"SUMMARY:(.*)", event)
        if start and summary:
            d = start.group(1)
            title = summary.group(1).strip().replace("\\,", ",").replace("\\;", ";")
            out.append({"title": title, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "status": "approved"})
    return out


def site_from_admin(site: str, env_path: str) -> list[dict] | None:
    """Every tournament including pending ones, or None if we can't log in."""
    try:
        import admin_edit
        env = admin_edit.load_env(env_path)
    except (ImportError, FileNotFoundError):
        return None
    if not env.get("ADMIN_USERNAME") or not env.get("ADMIN_PASSWORD"):
        return None

    try:
        opener = admin_edit.make_opener()
        admin_edit.login(opener, site, env["ADMIN_USERNAME"], env["ADMIN_PASSWORD"])
        page = admin_edit.get(opener, site.rstrip("/") + "/admin")
    except SystemExit as e:
        print(f"aviso: login no admin falhou ({e}); uso o .ics publico", file=sys.stderr)
        return None
    except Exception as e:
        print(f"aviso: admin inacessivel ({e}); uso o .ics publico", file=sys.stderr)
        return None

    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        if "/edit" not in row:
            continue
        cells = [re.sub(r"<[^>]+>", " ", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        cells = [" ".join(__import__("html").unescape(c).split()) for c in cells]
        if len(cells) < 4:
            continue
        day = re.match(r"(\d{2})/(\d{2})/(\d{4})", cells[0])
        if not day:
            continue
        d, m, y = day.groups()
        status = "pending" if "pending" in cells[3] else ("rejected" if "rejected" in cells[3] else "approved")
        out.append({"title": cells[1], "date": f"{y}-{m}-{d}", "status": status})
    # The dashboard lists pending rows in their own table as well as in "all".
    seen, deduped = set(), []
    for t in out:
        key = (normalize(t["title"]), t["date"])
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return deduped


def find_match(candidate: dict, have: list[dict], index: dict) -> dict | None:
    exact = index.get((normalize(candidate["title"]), candidate["date"]))
    if exact:
        return exact
    words = significant(candidate["title"])
    best, best_score = None, 0.0
    for t in have:
        if t["date"] != candidate["date"]:
            continue
        score = difflib.SequenceMatcher(
            None, normalize(candidate["title"]), normalize(t["title"])).ratio()
        # A shared distinctive word (a town, a club, an edition name) on the
        # same date is strong evidence on its own.
        if words and significant(t["title"]) & words:
            score = max(score, SIMILARITY)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= SIMILARITY else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", help="JSON written by the scrape/fetch scripts")
    p.add_argument("--site", default=DEFAULT_SITE)
    p.add_argument("--env", default=".env")
    p.add_argument("--out", help="write the missing tournaments to this JSON file")
    p.add_argument("--public-only", action="store_true",
                   help="use the public .ics even when admin credentials exist")
    args = p.parse_args()

    site = args.site.rstrip("/")
    have = None if args.public_only else site_from_admin(site, args.env)
    source_label = "admin (inclui pendentes)"
    if have is None:
        have, source_label = site_from_ics(site), "calendario.ics (so aprovados)"
    index = {(normalize(t["title"]), t["date"]): t for t in have}
    print(f"No site: {len(have)} torneios, via {source_label}")
    if any(t["status"] == "pending" for t in have):
        print(f"  ({sum(1 for t in have if t['status'] == 'pending')} ainda pendentes de aprovacao)")

    candidates, seen = [], set()
    for path in args.files:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        for t in loaded:
            key = (normalize(t.get("title", "")), t.get("date"))
            if key in seen:
                continue
            seen.add(key)
            t.setdefault("source", os.path.basename(path))
            candidates.append(t)
    print(f"Candidatos: {len(candidates)} de {len(args.files)} ficheiro(s)\n")

    today = date.today().isoformat()
    missing, covered, stale = [], [], []
    for c in candidates:
        if not c.get("date") or c["date"] <= today:
            stale.append(c)
        elif find_match(c, have, index):
            covered.append(c)
        else:
            missing.append(c)

    by_source = Counter(c["source"] for c in candidates)
    cov_by_source = Counter(c["source"] for c in covered)
    print("Cobertura por fonte:")
    for src, total in sorted(by_source.items()):
        n = cov_by_source[src]
        print(f"  {src:<28} {n:>3}/{total:<3} no site  ({100 * n // total if total else 0}%)")

    missing.sort(key=lambda t: t["date"])
    print(f"\nEM FALTA: {len(missing)}")
    for c in missing:
        flags = []
        if not c.get("district"):
            flags.append("sem distrito")
        if not c.get("location"):
            flags.append("sem local")
        if not c.get("time_control"):
            flags.append("sem ritmo")
        suffix = f"   [{', '.join(flags)}]" if flags else ""
        print(f"  {c['date']}  {c.get('source', '?'):<10} {c['title'][:62]}{suffix}")

    if stale:
        print(f"\nJa comecados ou sem data, ignorados: {len(stale)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(missing, f, ensure_ascii=False, indent=2)
        print(f"\n{len(missing)} escritos em {args.out} -- REVER antes de submeter (ver SCRAPING.md)")


if __name__ == "__main__":
    main()
