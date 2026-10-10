"""Fetch upcoming tournaments from the sources that aren't chess-results.com.

Writes the same JSON shape as `scrape_chess_results.py scrape`, so the output
of either can be reviewed and then fed to `scrape_chess_results.py submit`.

    python3 scripts/fetch_sources.py --out /tmp/sources.json          # all
    python3 scripts/fetch_sources.py --source fpx --out /tmp/fpx.json

Sources:

- **pct** — Portugal Chess Tour, the FPX's circuit of open tournaments.
- **axl** — Associação de Xadrez de Lisboa.

  Both run WordPress "The Events Calendar", which publishes a clean iCal feed
  with venue addresses. That is far more reliable than scraping their HTML,
  and it is how this script reads them. If another association turns out to
  run the same plugin, adding it is one line in SOURCES — look for a
  `/eventos/lista/?ical=1` or `/calendario/lista/?ical=1` URL.

- **fpx** — the federation's national calendar (championships and cups).
  A bespoke site with no feed, so this parses the HTML.

What it deliberately does NOT do: fill in `time_control` or `district`
reliably. Time controls live in regulation PDFs and vary per edition;
districts are guessed from the venue text and left empty when unsure. Both
must be checked by hand — see SCRAPING.md.

Standard library only, so it runs without the app's virtualenv.
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

USER_AGENT = "Mozilla/5.0 (compatible; torneiosdexadrez.pt scraper)"

SOURCES = {
    "pct": {
        "kind": "ical",
        "url": "https://portugalchesstour.fpx.pt/calendario/lista/?ical=1",
        "organizer": "",
        "label": "Portugal Chess Tour",
    },
    "axl": {
        "kind": "ical",
        "url": "https://axadrezlisboa.pt/eventos/lista/?ical=1",
        "organizer": "Associação de Xadrez de Lisboa",
        "label": "AX Lisboa",
    },
    "fpx": {
        "kind": "fpx",
        "url": "https://fpx.pt/site/calendario",
        "organizer": "Federação Portuguesa de Xadrez",
        "label": "Calendário Nacional FPX",
    },
}

# Municipality (or well-known venue town) -> district. Only the ones that have
# actually come up; the point is to catch the cases where a source files a
# tournament under the wrong district, not to be a complete gazetteer.
# Keys are compared accent- and case-insensitively.
MUNICIPALITY_DISTRICT = {
    "lisboa": "Lisboa", "sintra": "Lisboa", "oeiras": "Lisboa", "cascais": "Lisboa",
    "loures": "Lisboa", "odivelas": "Lisboa", "amadora": "Lisboa", "marvila": "Lisboa",
    "benfica": "Lisboa", "telheiras": "Lisboa", "lumiar": "Lisboa", "barcarena": "Lisboa",
    "vila franca de xira": "Lisboa", "torres vedras": "Lisboa", "alverca": "Lisboa",
    "seixal": "Setúbal", "amora": "Setúbal", "arrentela": "Setúbal", "barreiro": "Setúbal",
    "almada": "Setúbal", "caparica": "Setúbal", "setubal": "Setúbal", "montijo": "Setúbal",
    "palmela": "Setúbal", "sesimbra": "Setúbal", "samouco": "Setúbal",
    "porto": "Porto", "gaia": "Porto", "matosinhos": "Porto", "leca": "Porto",
    "penafiel": "Porto", "paredes": "Porto", "recarei": "Porto", "maia": "Porto",
    "gondomar": "Porto", "valongo": "Porto", "povoa de varzim": "Porto", "felgueiras": "Porto",
    "braga": "Braga", "guimaraes": "Braga", "barcelos": "Braga", "famalicao": "Braga",
    "riba de ave": "Braga", "fafe": "Braga", "esposende": "Braga",
    "aveiro": "Aveiro", "sao joao da madeira": "Aveiro", "feira": "Aveiro",
    "santa maria da feira": "Aveiro", "ovar": "Aveiro", "cucujaes": "Aveiro",
    "coimbra": "Coimbra", "figueira da foz": "Coimbra", "montemor-o-velho": "Coimbra",
    "soure": "Coimbra", "olivais": "Coimbra", "vila nova de ancos": "Coimbra",
    "leiria": "Leiria", "pombal": "Leiria", "caldas da rainha": "Leiria",
    "marinha grande": "Leiria", "nazare": "Leiria", "alcobaca": "Leiria",
    "faro": "Faro", "lagos": "Faro", "portimao": "Faro", "loule": "Faro",
    "albufeira": "Faro", "lagoa": "Faro", "silves": "Faro", "tavira": "Faro",
    "vila real de santo antonio": "Faro", "olhao": "Faro",
    "funchal": "Madeira", "madeira": "Madeira",
    "ponta delgada": "Açores", "acores": "Açores", "angra do heroismo": "Açores",
    "viseu": "Viseu", "castro daire": "Viseu", "mangualde": "Viseu",
    "sao pedro do sul": "Viseu", "lamego": "Viseu",
    "santarem": "Santarém", "tomar": "Santarém", "abrantes": "Santarém",
    "evora": "Évora", "montemor-o-novo": "Évora",
    "beja": "Beja", "portalegre": "Portalegre", "castelo branco": "Castelo Branco",
    "covilha": "Castelo Branco", "fundao": "Castelo Branco",
    "guarda": "Guarda", "seia": "Guarda", "loriga": "Guarda",
    "viana do castelo": "Viana do Castelo", "ponte de lima": "Viana do Castelo",
    "vila real": "Vila Real", "chaves": "Vila Real",
    "braganca": "Bragança", "mirandela": "Bragança",
}

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

_DEACCENT = str.maketrans("àáâãäçèéêëìíîïñòóôõöùúûü", "aaaaaceeeeiiiinooooouuuu")


def deaccent(value: str) -> str:
    return value.lower().translate(_DEACCENT)


def fetch(url: str, retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))


def guess_district(text: str) -> str | None:
    """District from a venue string, or None when nothing matches.

    Picks the match ending latest in the string, breaking ties by length.
    Addresses run venue, street, town, so the town is near the end — which is
    what keeps "Rua Camilo Castelo Branco 23, Arrentela, Seixal" in Setúbal
    instead of Castelo Branco, a street named after somewhere else being the
    classic way this goes wrong. The length tie-break then keeps
    "São João da Madeira" (Aveiro) from reading as Madeira, and
    "Vila Real de Santo António" (Faro) from reading as Vila Real.
    """
    if not text:
        return None
    flat = deaccent(text)
    best, best_rank = None, None
    for name, district in MUNICIPALITY_DISTRICT.items():
        for m in re.finditer(rf"\b{re.escape(name)}\b", flat):
            rank = (m.end(), len(name))
            if best_rank is None or rank > best_rank:
                best, best_rank = district, rank
    return best


def blank(source: str) -> dict:
    return {
        "title": "", "district": None, "date": None, "end_date": None,
        "location": "", "time_control": "", "organizer": "",
        "chess_results_url": None, "regulation_url": None,
        "registration_url": None, "link": None, "description": "",
        "source": source, "notes": "",
    }


# --- iCal (WordPress "The Events Calendar") ---------------------------------


def ics_unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\,", ",")
                 .replace("\\;", ";").replace("\\\\", "\\").strip())


def tidy_location(value: str) -> str:
    """Clean a venue address out of a calendar feed.

    The plugin emits the country, empty address slots, and the town twice
    ("Soure, Soure", "Coimbra, Coimbra") when venue and town are the same.
    """
    value = re.sub(r",?\s*Portugal\s*$", "", value.strip(), flags=re.I)
    parts = [p.strip() for p in value.split(",")]
    out = []
    for part in parts:
        if part and (not out or deaccent(part) != deaccent(out[-1])):
            out.append(part)
    return ", ".join(out).strip(" ,")


def parse_ical(body: str, source: str, default_organizer: str) -> list[dict]:
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"\n[ \t]", "", body)  # unfold continuation lines
    out = []
    for chunk in body.split("BEGIN:VEVENT")[1:]:
        chunk = chunk.split("END:VEVENT")[0]

        def prop(name: str) -> str:
            m = re.search(rf"^{name}(?:;[^:\n]*)?:(.*)$", chunk, flags=re.M)
            return ics_unescape(m.group(1)) if m else ""

        start_raw, end_raw = prop("DTSTART"), prop("DTEND")
        if not re.match(r"\d{8}", start_raw):
            continue
        start = datetime.strptime(start_raw[:8], "%Y%m%d").date()

        end = None
        if re.match(r"\d{8}", end_raw):
            end = datetime.strptime(end_raw[:8], "%Y%m%d").date()
            # All-day events use an exclusive DTEND (one past the last day).
            # Timed events carry a real end instant, so leave those alone.
            if "T" not in end_raw and end > start:
                end -= timedelta(days=1)
            if end <= start:
                end = None

        t = blank(source)
        t["title"] = prop("SUMMARY")
        t["date"] = start.isoformat()
        t["end_date"] = end.isoformat() if end else None
        t["location"] = tidy_location(prop("LOCATION"))
        t["district"] = guess_district(t["location"]) or guess_district(t["title"])
        t["organizer"] = prop("ORGANIZER") or default_organizer
        t["link"] = prop("URL") or None
        desc = re.sub(r"<[^>]+>", " ", prop("DESCRIPTION"))
        t["description"] = " ".join(html.unescape(desc).split())[:600]
        out.append(t)
    return out


# --- FPX national calendar (bespoke HTML) -----------------------------------


def fpx_event_links(page: str) -> list[str]:
    return sorted({
        u for u in re.findall(r'href="(https://fpx\.pt/site/calendario/[^"]+)"', page)
        if u.count("/") >= 6
    })


def fpx_content_lines(page: str) -> list[str]:
    """Visible text of an FPX event page, nav and footer stripped.

    The page is a nav-heavy template: real content starts at the first <h1>
    and ends at the share/back links.
    """
    i = page.find("<h1")
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page[i:i + 14000] if i > 0 else page, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    for stop in ("PARTILHAR NO FACEBOOK", "VOLTAR", "LOJA FPX"):
        if stop in lines:
            lines = lines[:lines.index(stop)]
            break
    return [x for x in lines if x not in ("download", "aqui", "aqui.", ".", ";")]


_MONTH_ALT = "|".join(MONTHS)
# "17 de outubro de 2026", "12 a 18 de julho de 2027", "29 agosto a 01 de
# setembro de 2027", "18 a 23 dezembro de 2026", "09 de maio 2027".
# Every "de" is optional -- the FPX pages drop them inconsistently, and
# requiring them silently lost the Mestres/Honra and Jovens Semi-Rápidas dates.
FPX_DATE_RE = re.compile(
    rf"(?:(\d{{1,2}})\s*(?:de\s+)?({_MONTH_ALT})?\s*(?:a|e)\s+)?"
    rf"(\d{{1,2}})\s+(?:de\s+)?({_MONTH_ALT})\s+(?:de\s+)?(\d{{4}})",
    flags=re.I)

# Template boilerplate and date lines that show up as the first content lines
# on FPX pages, ahead of (or instead of) the competition's own title.
FPX_BOILERPLATE = re.compile(
    rf"^(?:consulte a listagem|as partidas gravadas|a lista de campe|»|inscri"
    rf"|[-\s\d]*\d{{1,2}}\s*(?:de\s+)?(?:{_MONTH_ALT})\b"
    rf"|[-\s]*\d{{2}}/\d{{2}}/\d{{4}})", re.I)


def parse_fpx_dates(text: str) -> tuple[str | None, str | None]:
    """First date range on an FPX event page, as (start, end)."""
    numeric = re.findall(r"(\d{2})/(\d{2})/(\d{4})", text)
    if numeric:
        days = [date(int(y), int(m), int(d)) for d, m, y in numeric[:2]]
        return days[0].isoformat(), (days[1].isoformat() if len(days) > 1 and days[1] > days[0] else None)

    m = FPX_DATE_RE.search(text)
    if not m:
        return None, None
    d1, m1, d2, m2, year = m.groups()
    end = date(int(year), MONTHS[m2.lower()], int(d2))
    if not d1:
        return end.isoformat(), None
    first_month = MONTHS[(m1 or m2).lower()]
    # A range carries one year, on the end date; a first month later than the
    # last means it crosses New Year.
    first_year = int(year) - 1 if first_month > MONTHS[m2.lower()] else int(year)
    start = date(first_year, first_month, int(d1))
    return start.isoformat(), (end.isoformat() if end > start else None)


def parse_fpx_event(url: str, page: str, organizer: str) -> dict | None:
    lines = fpx_content_lines(page)
    if not lines:
        return None
    text = " ".join(lines)

    t = blank("fpx")
    # lines[0] is the page heading ("2026/2027 Supertaça"); the competition's
    # own title, usually in caps, follows it and reads better -- but so does a
    # pile of template boilerplate, so skip that first.
    body = [l for l in lines[1:] if not FPX_BOILERPLATE.match(l)]
    t["title"] = body[0] if body and len(body[0]) > len(lines[0]) else lines[0]
    t["date"], t["end_date"] = parse_fpx_dates(text)
    t["organizer"] = organizer
    t["link"] = url

    venue = next((l for l in lines if re.search(r"\d{4}-\d{3}|Rua |Avenida |Av\. |Pavilh|Complexo|Hotel|Sede ", l)), "")
    # Venue lines read "- 17 de outubro de 2026, <venue>" or
    # "- 10 e 11 de julho de 2027, <venue>"; drop the date, keep the venue.
    venue = re.sub(rf"^[-\s]*(?:\d{{1,2}}\s*(?:de\s+)?(?:{_MONTH_ALT})?\s*(?:a|e)\s+)?"
                   rf"\d{{1,2}}\s+(?:de\s+)?(?:{_MONTH_ALT})\s+(?:de\s+)?\d{{4}}\s*[,-]\s*",
                   "", venue, flags=re.I)
    t["location"] = re.sub(r"^[-\s]+", "", venue).strip()[:200]
    t["district"] = guess_district(venue) or guess_district(text[:400])

    regs = [u for u in re.findall(r'href="([^"]+\.pdf[^"]*)"', page, flags=re.I) if "campeoes" not in u]
    if regs:
        t["regulation_url"] = urllib.parse.urljoin(url, html.unescape(regs[0]))
    cr = re.findall(r'href="([^"]*chess-results[^"]*)"', page)
    if cr:
        t["chess_results_url"] = html.unescape(cr[0]).replace("&amp;", "&")
    if not t["location"]:
        t["notes"] = "Local ainda nao anunciado pela FPX."
    return t


def fetch_fpx(cfg: dict, delay: float) -> list[dict]:
    index = fetch(cfg["url"])
    links = fpx_event_links(index)
    print(f"  {len(links)} provas no calendario FPX", file=sys.stderr)
    out = []
    for url in links:
        try:
            t = parse_fpx_event(url, fetch(url), cfg["organizer"])
        except Exception as e:  # one bad page shouldn't lose the whole run
            print(f"  aviso: {url} falhou ({e})", file=sys.stderr)
            continue
        if t:
            out.append(t)
        time.sleep(delay)
    return out


# --- driver -----------------------------------------------------------------


def run(names: list[str], delay: float, horizon_days: int) -> list[dict]:
    today = date.today()
    horizon = (today + timedelta(days=horizon_days)).isoformat()
    out = []
    for name in names:
        cfg = SOURCES[name]
        print(f"{cfg['label']}...", file=sys.stderr)
        if cfg["kind"] == "ical":
            found = parse_ical(fetch(cfg["url"]), name, cfg["organizer"])
        else:
            found = fetch_fpx(cfg, delay)

        kept = []
        for t in found:
            if not t["date"]:
                print(f"  sem data, ignorado: {t['title'][:60]}", file=sys.stderr)
                continue
            if not (today.isoformat() < t["date"] <= horizon):
                continue
            kept.append(t)
        print(f"  {len(kept)} futuros (de {len(found)})", file=sys.stderr)
        out += kept
    return sorted(out, key=lambda t: (t["date"], t["title"]))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", action="append", choices=list(SOURCES) + ["all"], default=None)
    p.add_argument("--out", default="sources.json")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between FPX page fetches")
    p.add_argument("--horizon-days", type=int, default=365)
    args = p.parse_args()

    names = list(SOURCES) if not args.source or "all" in args.source else args.source
    tournaments = run(names, args.delay, args.horizon_days)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tournaments, f, ensure_ascii=False, indent=2)

    missing_district = sum(1 for t in tournaments if not t["district"])
    missing_venue = sum(1 for t in tournaments if not t["location"])
    print(f"\n{len(tournaments)} torneios escritos em {args.out}", file=sys.stderr)
    print(f"  {missing_district} sem distrito, {missing_venue} sem local, "
          f"{len(tournaments)} sem ritmo de jogo -- precisam de revisao manual", file=sys.stderr)


if __name__ == "__main__":
    main()
