"""Scrape upcoming Portuguese tournaments from chess-results.com and submit
them to the site's public /submeter form.

Two steps, so a human can review the candidates in between:

    python scripts/scrape_chess_results.py scrape --out candidates.json
    # review/edit candidates.json (drop internal/youth-only events, tidy titles)
    python scripts/scrape_chess_results.py submit candidates.json \
        --site https://torneiosdexadrez.pt

Submissions land in the admin's pending queue like any other. `submit` skips
tournaments already published (matched by title + date against
/calendario.ics) and ones it already sent before (recorded in a ledger file),
so re-running it is safe.

Standard library only, so it runs without the app's virtualenv.
"""

import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

USER_AGENT = "Mozilla/5.0 (compatible; torneiosdexadrez.pt scraper)"
FED_URL = "https://chess-results.com/fed.aspx?lan=10&fed=POR"
TNR_URL = "https://chess-results.com/tnr{id}.aspx?lan=10"

# chess-results "bdld1" codes for Portugal. 0-2 (other/national) carry no
# district, so tournaments only listed there get district=None.
DISTRICTS = {
    3: "Aveiro", 4: "Beja", 5: "Braga", 6: "Bragança", 7: "Castelo Branco",
    8: "Coimbra", 9: "Évora", 10: "Faro", 11: "Guarda", 12: "Leiria",
    13: "Lisboa", 14: "Portalegre", 15: "Porto", 16: "Santarém",
    17: "Setúbal", 18: "Viana do Castelo", 19: "Vila Real", 20: "Viseu",
    21: "Açores", 22: "Madeira",
}

# combo_tur_sel values: 2 = starting in the next 7 days, 3 = starting later.
UPCOMING_SELECTIONS = ("2", "3")


def fetch(url: str, data: dict | None = None, retries: int = 3) -> str:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            # chess-results occasionally drops connections under load
            if attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))


def _hidden(page: str, name: str) -> str:
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % name, page)
    return html.unescape(m.group(1)) if m else ""


def list_upcoming_ids(url: str, delay: float) -> list[str]:
    """Tournament ids on a fed.aspx listing, filtered to not-yet-started ones.
    The filter is an ASP.NET postback, so the form's hidden state is echoed."""
    page = fetch(url)
    time.sleep(delay)
    ids = []
    for selection in UPCOMING_SELECTIONS:
        result = fetch(url, {
            "__EVENTTARGET": "combo_sel",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": _hidden(page, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden(page, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _hidden(page, "__EVENTVALIDATION"),
            "combo_tur_sel": selection,
            "combo_sort": "0",
        })
        ids += re.findall(r'tnr(\d+)\.aspx\?lan=10"', result)
        time.sleep(delay)
    return list(dict.fromkeys(ids))


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_time_control(raw: str) -> str:
    """'90 minutes + 30 seconds increment' -> '90 + 30' (the site's style).
    Anything that isn't a plain base+increment (multi-period '90/40 + 30',
    knockout '5 KO') is returned unchanged for a human to tidy."""
    s = raw.lower()
    if re.search(r"/\s*40|\bmoves\b|\blances\b", s):
        return raw
    s = re.sub(r"(desde o|from|since) (lance|move) 1", "", s)
    if hours := re.match(r"\s*(\d+)\s*h\s*(\d+)?\s*m?", s):
        s = str(int(hours.group(1)) * 60 + int(hours.group(2) or 0)) + s[hours.end():]
    nums = re.findall(r"\d+", s)
    if len(nums) == 2 and re.search(r"\+|incre|with|com", s):
        return f"{nums[0]} + {nums[1]}"
    return raw


def parse_tournament(tnr_id: str, page: str) -> dict:
    fields = {
        _text(k): v
        for k, v in re.findall(r'<td class="CR">([^<]+)</td><td class="CR">(.*?)</td></tr>', page, flags=re.I)
    }
    title_m = re.search(r"<h2>(.*?)</h2>", page, flags=re.S)
    title = _text(title_m.group(1)) if title_m else ""

    def field(prefix: str) -> str:
        for k, v in fields.items():
            if k.startswith(prefix):
                return _text(v)
        return ""

    dates = re.findall(r"(\d{4})/(\d{2})/(\d{2})", field("Data"))
    start = date(*map(int, dates[0])).isoformat() if dates else None
    end = date(*map(int, dates[-1])).isoformat() if len(dates) > 1 else None

    raw_tc = field("Tempo de reflexão")
    tc_kind = re.search(r"\((.*?)\)", next((k for k in fields if k.startswith("Tempo de reflexão")), ""))
    official = re.search(r'href="([^"]+)"[^>]*>Página oficial do organizador', page)
    regulation = re.search(r'href="([^"]*UploadData\.aspx[^"]*)"', page)

    page_url = TNR_URL.format(id=tnr_id)
    rounds = field("Number of rounds")
    details = [
        tc_kind.group(1) if tc_kind else "",
        f"{rounds} rondas" if rounds else "",
        field("Tournament type"),
        field("Cálculo de Rating"),
    ]
    description = ", ".join(d for d in details if d)
    if end and end != start:
        description += f". De {start} a {end}."

    return {
        "chess_results_id": tnr_id,
        "title": title,
        "district": None,
        "date": start,
        "end_date": end,
        "location": field("Local"),
        "time_control": parse_time_control(raw_tc),
        "time_control_raw": raw_tc,
        "organizer": field("Organizador"),
        "chess_results_url": page_url,
        # chess-results links uploaded regulations relative to the page
        "regulation_url": urllib.parse.urljoin(page_url, html.unescape(regulation.group(1))) if regulation else None,
        "registration_url": None,
        "link": urllib.parse.urljoin(page_url, html.unescape(official.group(1))) if official else None,
        "description": description,
        "last_update": _text(m.group(1)) if (m := re.search(r"Última Atualização(.*?),", page)) else "",
    }


def scrape(delay: float) -> list[dict]:
    district_of = {}
    for code, name in DISTRICTS.items():
        for tnr_id in list_upcoming_ids(f"{FED_URL}&bdld1={code}", delay):
            district_of.setdefault(tnr_id, name)
        print(f"  {name}: {sum(1 for d in district_of.values() if d == name)}", file=sys.stderr)

    all_ids = list_upcoming_ids(FED_URL, delay)
    all_ids += [i for i in district_of if i not in all_ids]
    print(f"{len(all_ids)} upcoming tournaments, fetching details...", file=sys.stderr)

    today = date.today()
    # Abandoned tournaments with a mistyped year (2919, 3014...) show up as
    # "starting later" forever; nothing real is announced this far ahead.
    horizon = (today + timedelta(days=365)).isoformat()
    tournaments = []
    for tnr_id in all_ids:
        t = parse_tournament(tnr_id, fetch(TNR_URL.format(id=tnr_id) + "&turdet=YES"))
        t["district"] = district_of.get(tnr_id)
        time.sleep(delay)
        if not t["date"] or not today.isoformat() < t["date"] <= horizon:
            print(f"  skip {tnr_id} (start {t['date']}): {t['title']}", file=sys.stderr)
            continue
        tournaments.append(t)
    return sorted(tournaments, key=lambda t: t["date"])


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(c for c in value if c.isalnum())


def published_keys(site: str) -> set[tuple[str, str]]:
    """(title, date) of every approved tournament, from the site's ICS feed."""
    ics = fetch(site.rstrip("/") + "/calendario.ics").replace("\r\n ", "")
    keys = set()
    for event in ics.split("BEGIN:VEVENT")[1:]:
        start = re.search(r"DTSTART;VALUE=DATE:(\d{8})", event)
        summary = re.search(r"SUMMARY:(.*)", event)
        if start and summary:
            d = start.group(1)
            title = summary.group(1).strip().replace("\\,", ",").replace("\\;", ";")
            keys.add((normalize_title(title), f"{d[:4]}-{d[4:6]}-{d[6:]}"))
    return keys


FORM_FIELDS = [
    "title", "district", "date", "location", "time_control", "organizer",
    "chess_results_url", "regulation_url", "registration_url", "link", "description",
]
URL_FIELDS = ["chess_results_url", "regulation_url", "registration_url", "link"]


def submit(path: str, site: str, ledger_path: str, submitter: str, dry_run: bool) -> int:
    with open(path, encoding="utf-8") as f:
        tournaments = json.load(f)
    try:
        with open(ledger_path, encoding="utf-8") as f:
            ledger = json.load(f)
    except FileNotFoundError:
        ledger = {}

    published = published_keys(site)
    failures = 0
    for t in tournaments:
        key = f"{normalize_title(t['title'])}|{t['date']}"
        missing = [f for f in ("title", "district", "date", "location", "time_control") if not t.get(f)]
        if missing:
            print(f"SKIP (falta {', '.join(missing)}): {t['title']}")
            continue
        if (normalize_title(t["title"]), t["date"]) in published:
            print(f"SKIP (ja publicado): {t['date']} {t['title']}")
            continue
        if key in ledger or (t.get("chess_results_url") and t["chess_results_url"] in ledger.values()):
            print(f"SKIP (ja submetido): {t['date']} {t['title']}")
            continue
        if dry_run:
            print(f"DRY-RUN: {t['date']} [{t['district']}] {t['title']}")
            continue

        form = {f: (t.get(f) or "") for f in FORM_FIELDS}
        for f in URL_FIELDS:
            if form[f] and not form[f].startswith(("http://", "https://")):
                print(f"  aviso: {f} ignorado (nao e http/https): {form[f]}")
                form[f] = ""
        form |= {"submitted_by_name": submitter, "submitted_by_email": "", "confirmacao": ""}
        req = urllib.request.Request(
            site.rstrip("/") + "/submeter",
            data=urllib.parse.urlencode(form).encode(),
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                ok = resp.geturl().endswith("/submeter/obrigado")
        except urllib.error.HTTPError as e:
            errors = re.findall(r'<p class="error">(.*?)</p>', e.read().decode("utf-8", "replace"))
            print(f"FALHOU ({e.code} {errors}): {t['title']}")
            failures += 1
            continue
        if ok:
            ledger[key] = t.get("chess_results_url") or ""
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, ensure_ascii=False, indent=2)
            print(f"OK: {t['date']} [{t['district']}] {t['title']}")
        else:
            print(f"FALHOU (resposta inesperada): {t['title']}")
            failures += 1
        time.sleep(1)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scrape = sub.add_parser("scrape", help="write upcoming chess-results.com tournaments to JSON")
    p_scrape.add_argument("--out", default="candidates.json")
    p_scrape.add_argument("--delay", type=float, default=1.0, help="seconds between requests")

    p_submit = sub.add_parser("submit", help="POST a reviewed JSON file to /submeter")
    p_submit.add_argument("file")
    p_submit.add_argument("--site", default="https://torneiosdexadrez.pt")
    p_submit.add_argument("--ledger", default="submitted.json", help="remembers what was already sent")
    p_submit.add_argument("--submitter", default="Importacao automatica (chess-results.com)")
    p_submit.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.cmd == "scrape":
        tournaments = scrape(args.delay)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(tournaments, f, ensure_ascii=False, indent=2)
        print(f"{len(tournaments)} torneios escritos em {args.out}", file=sys.stderr)
    else:
        sys.exit(1 if submit(args.file, args.site, args.ledger, args.submitter, args.dry_run) else 0)


if __name__ == "__main__":
    main()
