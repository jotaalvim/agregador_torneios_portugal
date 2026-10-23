# Importing tournaments

How to find upcoming Portuguese chess tournaments and get them onto the site,
written for whoever does the next import run.

The scripts fetch, compare and submit. A human decides what is worth
publishing and fills in what the sources don't carry. That review step is not
optional — see [Traps](#traps) for why.

## The run

```bash
# 1. fetch everything upcoming
python3 scripts/scrape_chess_results.py scrape --out /tmp/cr.json   # ~3 min
python3 scripts/fetch_sources.py --out /tmp/sources.json            # ~1 min

# 2. what is NOT on the site yet?
python3 scripts/gaps.py /tmp/cr.json /tmp/sources.json --out /tmp/missing.json

# 3. READ /tmp/missing.json and edit it — see Review checklist below.
#    Expect to fill in time controls by hand: no source carries them reliably.

# 4. dry run, then send. Lands in the pending queue; nothing goes live.
python3 scripts/scrape_chess_results.py submit /tmp/missing.json --dry-run
python3 scripts/scrape_chess_results.py submit /tmp/missing.json
```

Then open `/admin` and approve, edit or reject each one.

All four scripts use only the Python standard library, so they run without the
app's virtualenv. The tests need pytest (`.venv/bin/pytest tests/`).

## The scripts

| Script | What it does |
| --- | --- |
| `scrape_chess_results.py` | `scrape` pulls chess-results.com; `submit` POSTs a reviewed file to `/submeter` |
| `fetch_sources.py` | The other sources: Portugal Chess Tour, AX Lisboa, FPX national calendar |
| `gaps.py` | Diffs candidates against the site; prints coverage and what's missing |
| `admin_edit.py` | Fixes tournaments already on the site |

`fetch_sources.py` and `scrape_chess_results.py scrape` write the same JSON
shape, so `gaps.py` and `submit` take either.

### Deduplication

`submit` skips a tournament twice over, so re-running is safe:

- **already published** — title + date against `/calendario.ics`
- **already sent** — `scripts/submitted.json`, the ledger

The ledger key is a normalized title (lowercased, accents and punctuation
stripped) plus the date. **A retitled tournament therefore looks new.** If you
rename something between runs you will submit a duplicate — which is what
`gaps.py` is for: it matches loosely (same date plus a shared distinctive
word), so it catches renames the ledger misses.

### `gaps.py` and the pending queue

It reads the site from the **admin dashboard** when `.env` has credentials, so
tournaments still awaiting approval count as covered. Without credentials it
falls back to the public `.ics`, which only lists approved ones — then
anything pending looks missing. `--public-only` forces the fallback.

Matching is deliberately loose. A false "already there" silently drops a
tournament; a false "missing" only costs you a look.

### `admin_edit.py`

```bash
python3 scripts/admin_edit.py --find "Benfica"        # get the id
python3 scripts/admin_edit.py 34 --show               # current values
python3 scripts/admin_edit.py 34 --set "location=Pavilhão X, Lisboa" --dry-run
```

The admin edit form rewrites **every** column from the POST body, so anything
left out is stored empty. The script reads the current values first and
overrides only the fields you name — don't hand-roll that POST.

**Login is rate-limited to 5 attempts per 15 minutes per IP**, and each
invocation logs in once. For several edits, write one script that imports
`admin_edit`, logs in once and loops. Test against a throwaway instance first:

```bash
DATABASE_URL=sqlite:///tmp/t.db ADMIN_USERNAME=admin ADMIN_PASSWORD=testpw \
  SECRET_KEY=test SESSION_HTTPS_ONLY=false \
  .venv/bin/uvicorn app.main:app --port 8766
```

## Sources

### chess-results.com — the bulk of the listings

Everything `scrape_chess_results.py` uses. Date, venue, time control,
organizer, rounds, often a regulation PDF.

- Federation listing: `https://chess-results.com/fed.aspx?lan=10&fed=POR`
  (`lan=10` is Portuguese)
- Per-district: `&bdld1=N`, codes 3–22 for Aveiro…Madeira (see `DISTRICTS` in
  the script). Codes 0–2 are other/national and carry **no** district.
- Detail page: `tnr<ID>.aspx?lan=10&turdet=YES` — `turdet=YES` renders the
  details table.

**The "upcoming" filter is an ASP.NET postback, not a query string**: you POST
the form back with its own hidden state echoed (`__VIEWSTATE`,
`__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`) plus `combo_tur_sel=2` (within 7
days) or `3` (later). `list_upcoming_ids()` does this. The server drops
connections under load, so `fetch()` retries.

Regulations are linked **relative to the page** (`UploadData.aspx?...`) and
must be absolutized or the site rejects them.

### Portugal Chess Tour and AX Lisboa — iCal feeds

Both run WordPress "The Events Calendar", which publishes a clean iCal feed
with venue addresses:

- `https://portugalchesstour.fpx.pt/calendario/lista/?ical=1`
- `https://axadrezlisboa.pt/eventos/lista/?ical=1`

Reading the feed beats scraping their HTML, and it is what `fetch_sources.py`
does. **If another association turns out to run the same plugin, adding it is
one line in `SOURCES`** — look for a `/eventos/lista/?ical=1` or
`/calendario/lista/?ical=1` URL. That is the cheapest way to widen coverage.

The Portugal Chess Tour is the FPX's circuit of open tournaments and was
**missed entirely on the first import**; it then corrected venues, organizers
and time controls that neither chess-results nor the main FPX site had. It
often links the *previous* edition's regulation — the dates and venue on the
page are current, an attached PDF may be a year old.

iCal all-day events use an **exclusive `DTEND`** (one past the last day).
Getting that wrong adds a phantom day to every multi-day tournament.

### FPX — the national calendar

`https://fpx.pt/site/calendario`, the authoritative list of national
championships and cups. A bespoke site with no feed, so `fetch_sources.py`
parses the HTML: event pages are `/site/calendario/<prova>/<edicao>`, and the
content starts at the first `<h1>` and ends at the share/back links.

Many 2027 events have a confirmed date but **no venue yet**. Some pages carry
a stale edition number or a previous season's regulation — check the
regulation's year against the event. The pages also drop the "de" particles
inconsistently (`18 a 23 dezembro de 2026`), which is why the date regex
treats every one of them as optional.

Time controls live in the regulation PDFs. Extract with `pdftotext -layout`.

### FIDE — registrations, earlier than anywhere else

- `https://ratings.fide.com/a_tournaments.php?country=POR` — since October
  2026 this returns an **empty body** unless the request carries
  `X-Requested-With: XMLHttpRequest`; with it, it returns JSON
  (`{"data": [[id, name, city, system, start, end, …], …]}`).
- `https://ratings.fide.com/tournament_information.phtml?event=<N>` — parse
  the `<tr>` pairs for name, city, dates, system, time control, arbiter.

Events are registered here before they reach chess-results, so this is where
you find things early. Not automated: the yield is low and needs judgement.

**FIDE's time controls are often the category default rather than the real
one.** When FIDE disagrees with chess-results or a regulation, FIDE is usually
wrong. Cities are sometimes placeholders too — the European championships in
Portimão were listed as "Lisbon".

### FPX club platform — open-tournament list, iCal feed, regulations

Found 2026-10-17. Most of `clubes.fpx.pt` sits behind a club login, but three
parts are public and carried tournaments that no other source had yet:

- **Open-tournament list:** `https://clubes.fpx.pt/listagens/lista_torneios_busca.php`,
  the FPX's homologated opens with dates, rhythm, organizer, chess-results and
  regulation links. The HTML never closes its `<tr>`/`<td>` tags, so split on
  `<tr class=linfil>`.
- **iCal feed:** `http://clubes.fpx.pt/fpxcalendario_ics/<oficiais>:<outras>:<flags>`,
  built by the form at `https://clubes.fpx.pt/fpxcalendario`. Each list is
  two-digit region codes from `00` (Nacionais) to `21` (Internacionais); the
  flags `J`, `V`, `F` add youth, veteran and women's events. Everything is
  `000102…21:000102…21:FVJ`. Events are timed rather than all-day, and the
  description names the regulation. `parse_ical` reads it, so adding it is one
  line in `SOURCES`.
- **Regulations:** `/provas/regulamentos/<DIST>_<season end>_<code>.pdf` for
  association championships (`POR_2027_CDSR` is AX Porto's 2026/27
  semi-rápidas) and `/provas/homologadas/<file>.pdf` for opens. Links can be
  mistyped: the list pointed at `ALG_2007_CDSR.pdf`, the file is
  `ALG_2027_CDSR.pdf`. Its chess-results links can be wrong too: Lagoa's
  "Dia Mundial do Xadrez" pointed at `tnr1496708`, a GXP circuit event
  (2026-10-23). Check the title on the page you land on.

The 2026-10-17 run found the Philanderkas (Chaves) and Cucujães opens, the
Setúbal and Algarve semi-rápidas championships, and the remaining dates of the
GXP Campeãs Mundiais circuit here. The first four were also on FIDE; none were
on chess-results yet.

## Coverage

`gaps.py` prints a figure per source. As of the October 2026 run, against a
site with 79 tournaments:

| Source | Covered |
| --- | --- |
| Portugal Chess Tour | 8/8 |
| FPX national calendar | 31/37 |
| AX Lisboa | 7/29 |

The AX Lisboa number is low because its feed carries school tournaments and
round-by-round league fixtures that are deliberately not published here — see
[What to leave out](#what-to-leave-out). Don't chase 100%; read what's missing
and decide.

## Field conventions

### Time control — read this carefully

Portuguese chess vocabulary does not map onto the English terms:

| Portuguese | What it means | Typical value |
| --- | --- | --- |
| **Clássicas** | classical | `90 + 30` |
| **Semi-rápidas** | rapid | `10 + 5`, `15 + 5` |
| **Rápidas** | **blitz** | `3 + 2` |

"Rápidas" is *blitz*, not rapid. This is the easiest mistake to make and it
has already happened: the Braga tournament went up as `90 + 30` when it is
`15 + 3`.

Format is `minutes + seconds-of-increment`. No increment → `15 + 0`.
Multi-period controls stay long-form: `90/40 + 30 + 30`.

`parse_time_control()` normalizes the many ways chess-results writes this and
deliberately leaves anything it doesn't recognise untouched, rather than
inventing a plausible-looking wrong answer. **No feed carries a time control
at all** — `fetch_sources.py` leaves it empty so the review step and `gaps.py`
both flag it.

### Everything else

- **title** — official name, cleaned. Strip embedded dates, times and prize
  clutter (`(4 de Outubro, Domingo, 15:00)`, `26/11 - 02/12 (13000€ IN
  PRIZES)`). Convert ALL CAPS to normal capitalization. Keep the edition
  number. Max 200 chars.
- **district** — from the venue's **municipality**, not from whatever category
  the source filed it under. One of the 20, or `Nacional`.
- **location** — venue + town, and it must name the town. Max 200.
- **date** — `YYYY-MM-DD`, the start date.
- **description** — 1–3 short sentences in Portuguese: format, rounds, start
  time, date range, who may enter, rating.
- URLs must start with `http://` or `https://`.

### The `Nacional` district

National championships with no announced venue use `Nacional`, with
`Local a anunciar pela FPX` as the location. This matches how the FPX and
chess-results categorize (chess-results `bdld1=2` is literally "Nacional").
Move them to the real district once a venue is published.

## Traps

Things that have actually gone wrong.

- **chess-results districts are sometimes wrong.** A Penafiel tournament was
  filed under Santarém; it is Porto.
- **Streets named after other places.** "Rua Camilo Castelo Branco 23,
  Arrentela, Seixal" is in Setúbal, not Castelo Branco. `guess_district()`
  handles this by taking the match that ends latest in the string, since
  addresses run venue, street, town — but it is still a guess. Check it.
- **Mistyped years.** chess-results has abandoned entries dated 2919, 2925 and
  3014 that show up forever under "starting later". The scraper drops anything
  starting more than 365 days out. Watch for dates that are merely a *season*
  off — a "2025/2026" event dated 2027.
- **Relative regulation URLs** — see chess-results above.
- **Multi-section events.** `Sub8+Sub10` and `Sub12+Sub14` of the same youth
  tournament, same venue, date and time control, are one entry; put the second
  chess-results link in the description. Different time controls or dates stay
  separate — the site lists "CD Equipas Rápidas" and "CD Equipas Semi-Rápidas"
  separately on purpose.
- **Our own data can be wrong.** The Lagos open sat on the site with the wrong
  date (03/10 instead of 31/10–07/11) and "Faro" as its location, so it showed
  as finished before it had started. When a source disagrees with what is
  published, check the regulation before assuming the source is wrong.

## What to leave out

- Club-internal tournaments (members only)
- School sport — CLDE events, anything titled by school year
  ("7º 8º e 9º Anos"), and the AX Lisboa "Torneio Escolar" series
- Events outside Portugal, and online-only events
- Anything starting on or before today
- Dates that cannot be confirmed on a page you actually opened

**Team league competitions** (the national 1ª/2ª/3ª Divisão, Taça de Portugal,
and AX Lisboa's "CD Clássicas Equipas ... - R1/R2/R3") are fixture lists of
many rounds spread over months at each club's home venue — their feed entries
literally give the location as "casa-fora". They don't fit one-row-per-event.

The Taça da AXL *was* published, as a single entry dated to its first round,
because that round has a known venue. **This is an unresolved inconsistency** —
decide which way you want it before the next run, because the feed will keep
offering both.

## Dead ends

Checked October 2026, nothing current; rows with a date were re-checked that
day. Skip unless you have reason to think they changed.

| Source | State |
| --- | --- |
| chessmanager.com | tournament search still returns 403 to scripts (2026-10-23) |
| tornelo.com, chess.com/events, lichess broadcasts | nothing for Portugal OTB; tornelo now returns 200 but is a JavaScript shell with no listings (2026-10-23) |
| chess-calendar.eu | now returns 200, but only re-lists chess-results and FIDE (2026-10-23) |
| clubes.fpx.pt | login-only, **except** the open-tournament list and iCal feed (see Sources, 2026-10-17) |
| axaveiro.pt | now times out (2026-10-23) |
| xadrez64.com | connection times out (2026-10-23) |
| axbraga.weebly.com | last updated 2018 (2026-10-23) |
| axporto.weebly.com | calendar stops at 2024/25 (2026-10-23); AXP's 2026/27 regulations are on clubes.fpx.pt |
| gxp.pt | no usable content without a browser (2026-10-23); the GXP circuit regulation is on clubes.fpx.pt |
| lecachessopen.com, espacomusas.pt, art-telheiras.pt, axsetubal.pt | no Events Calendar iCal feed, 404 (2026-10-23) |
| academiaxadrezmaia.pt, gdblxadrez.pt, maiachessopen.com | domains do not resolve (2026-10-23); the Academia de Xadrez da Maia posts its regulations on chess-results |
| Web search (postal.pt, viralagenda.com, montepio.org) | only past editions — Circuito Xadrez no Museu, Lagoa's Ibn Ammar circuit, Maia Blitz Cup; Montepio's rápidas are for members (2026-10-23) |
| ACXA (Algarve), AXRAM (Madeira), AXRAA (Açores) | no 2026/27 calendar online; ACXA's championship regulation is on clubes.fpx.pt |
| AXDC, Leiria, Guarda, Castelo Branco, Santarém | no calendar online |
| Municipal agendas, juntas de freguesia, local press | almost nothing; not worth a sweep. Matosinhos' sports agenda did list the GD Basquete de Leça open (2026-10-17) |

The AX Setúbal 2026/27 calendar PDF exists but its table is misaligned
(17 events against 14 dates) — don't trust rows read out of it without
cross-checking each one.

**When re-checking these, note the date here.** A dead-ends list with no dates
rots into "check everything again".

## Review checklist

Before `submit`, go through the JSON:

- [ ] Starts after today, and the year is plausible
- [ ] District matches the venue's municipality
- [ ] Location names the town
- [ ] Time control is filled in, in `X + Y` form, and
      **Rápidas = 3 + 2, Semi-rápidas = 10 + 5**
- [ ] Title has no leftover dates, times or prize text, and is not ALL CAPS
- [ ] Description is in Portuguese
- [ ] Every URL is http(s) and is the page it claims to be
- [ ] Not already on the site under a slightly different name
- [ ] Open to the public or to any federated player

Then `--dry-run`, read the output, and only then submit.

## Tests

```bash
.venv/bin/pytest tests/test_scraping.py
```

Covers the parsers the import depends on — time controls, FPX date formats,
district guessing, iCal, ledger normalization — using strings taken verbatim
from real source pages. **Run these after touching any parser.** They already
caught the street-name district bug above.
