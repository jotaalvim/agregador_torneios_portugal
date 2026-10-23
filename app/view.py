import calendar as _calendar
import hashlib
import json
import re
from datetime import date

from markupsafe import Markup, escape

from .models import Correction, CorrectionStatus, Tournament
from .utils import deaccent, slugify

MONTH_NAMES_PT = [
    "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
]
# Indexed by date.weekday() (Monday = 0), for the date tile in the list.
WEEKDAYS_PT = ["SEG", "TER", "QUA", "QUI", "SEX", "SAB", "DOM"]

# Colour slot per district, used by the calendar grid (see --district-N in
# style.css). The mapping is by slug and deliberately fixed: a district keeps
# its colour no matter which other districts are on screen, so filtering the
# calendar never repaints the ones that remain.
#
# Every district gets its own slot. Twenty mutually-distinguishable colours do
# not exist -- the palette was searched for maximum worst-pair separation and
# still lands at ~11 Delta E (OKLab x100) on its closest pair, under the ~15
# that full-colour vision needs, and ~6 under colour-blind simulation. Colour
# is therefore a secondary cue here, never the only one: the district name is
# in every day's tooltip and aria-label, next to every legend dot, and in the
# filters.
#
# Slot order is not arbitrary. The palette search hands out its best-separated
# colours first, so this list runs from the districts that fill most of this
# calendar down to the ones that rarely appear. The pairs that ended up closest
# together (slot 1 vs 20, for one) are the ones least likely to share a screen.
DISTRICT_SLOTS = {
    "braga": 1,
    "porto": 2,
    "viana-do-castelo": 3,
    "vila-real": 4,
    "braganca": 5,
    "aveiro": 6,
    "lisboa": 7,
    "coimbra": 8,
    "viseu": 9,
    "guarda": 10,
    "leiria": 11,
    "santarem": 12,
    "setubal": 13,
    "castelo-branco": 14,
    "faro": 15,
    "evora": 16,
    "portalegre": 17,
    "beja": 18,
    "madeira": 19,
    "acores": 20,
}
# Anything outside the list above (a national event, a typo, a new region)
# falls back to the neutral grey rather than borrowing a colour that already
# means a specific district.
DISTRICT_SLOT_OTHER = 0


def district_slot(name: str) -> int:
    return DISTRICT_SLOTS.get(slugify(name), DISTRICT_SLOT_OTHER)


_SECONDS_MARK = re.compile(r"(?<=\d)(?:''|\")")
_MINUTES_MARK = re.compile(r"(?<=\d)'")


def time_control_display(text: str) -> str:
    """Organizers type minutes and seconds with the keyboard's ' and '' (or ");
    print them as the prime marks they stand for: 90' + 30'' -> 90′ + 30″.
    Only a mark right after a digit is touched, so an apostrophe inside a
    word stays an apostrophe. Display only -- the stored text, the .ics feed
    and the share text keep what was typed."""
    return _MINUTES_MARK.sub("′", _SECONDS_MARK.sub("″", text))


def time_control_html(text: str) -> Markup:
    """time_control_display(), HTML-escaped, with each minute mark wrapped so
    the stylesheet can pull in the wide right margin Barlow Condensed draws
    after ′ (.tc-min in style.css) -- left alone, "12′ + 3″" reads as
    "12′  + 3″". Escaping comes first, so organizer-typed text can't inject
    markup; ′ never occurs inside an entity, so wrapping it afterwards is safe."""
    escaped = str(escape(time_control_display(text)))
    return Markup(escaped.replace("′", '<span class="tc-min">′</span>'))


# The ritmo filter: time controls grouped the way Portuguese chess names them,
# slowest first as in SCRAPING.md's table. "Rapidas" is blitz, not rapid (the
# trap that table warns about), so each chip also shows the usual ritmo of its
# group (`hint`).
TIME_CONTROLS = {
    "classicas": {"label": "Classicas", "hint": "90+30"},
    "semi-rapidas": {"label": "Semi-rapidas", "hint": "10+5"},
    "rapidas": {"label": "Rapidas", "hint": "3+2"},
}

_TC_HOURS = re.compile(r"(\d+)\s*h(?:oras?)?(?![a-z])\s*(\d{1,2})?")
_TC_BASE_INCREMENT = re.compile(r"(\d+)(?:\s*/\s*\d+)?[^\d+]*\+\s*(\d+)")
_TC_BASE_ONLY = re.compile(r"(\d+)\s*(?:['′’]|m(?:in\w*)?\b)")


def time_control_minutes(text: str) -> int | None:
    """Minutes per player for a 60-move game, the base plus 60 times the
    increment, which is what FIDE tells blitz, rapid and classical apart by.
    Reads the first base + increment in the text, wherever it is ("90' +
    30''/lance", "11 rondas, 3+2"), hours ("1h30 + 30") and a base on its own
    ("15 min"). "90/40 + 30 + 30" reads as 90 + 30: short of the real total,
    but its first period alone makes it classical. None with no time in it."""
    s = _TC_HOURS.sub(lambda m: f"{int(m[1]) * 60 + int(m[2] or 0)} min", deaccent(text))
    if m := _TC_BASE_INCREMENT.search(s):
        return int(m[1]) + int(m[2])
    if m := _TC_BASE_ONLY.search(s):
        return int(m[1])
    return None


def time_control_category(text: str) -> str | None:
    """The TIME_CONTROLS group of a ritmo, by FIDE's limits: blitz up to 10
    minutes, rapid under 60, classical from 60. None when it has no time in
    it; that tournament then only shows with no ritmo picked."""
    minutes = time_control_minutes(text)
    if minutes is None:
        return None
    if minutes <= 10:
        return "rapidas"
    if minutes < 60:
        return "semi-rapidas"
    return "classicas"


def time_control_slug(value: str) -> str:
    """The TIME_CONTROLS key a ?ritmo= value names, or "" for none: an unknown
    one shows every ritmo rather than an empty list."""
    slug = slugify(value)
    return slug if slug in TIME_CONTROLS else ""


def share_text(t: Tournament, page_url: str | None = None) -> str:
    """Plain-text summary formatted for sharing (WhatsApp, clipboard, etc):
    date/district/title on the first line, then ritmo and whichever links
    are actually set, each on its own line. `page_url` is the tournament's
    own absolute URL, provided by the caller since view.py has no access
    to the request/domain."""
    date_short = f"{t.date.day} {MONTH_NAMES_PT[t.date.month - 1].capitalize()}"
    lines = [f"{date_short} - {t.district} | {t.title}", t.time_control]
    if t.registration_url:
        lines.append(f"inscrição online: {t.registration_url}")
    if t.regulation_url:
        lines.append(f"regulamento: {t.regulation_url}")
    if t.chess_results_url:
        lines.append(f"lista participantes: {t.chess_results_url}")
    if page_url:
        lines.append(f"página: {page_url}")
    return "\n\n".join(lines)


def row(t: Tournament, today: date, page_url: str | None = None) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "district": t.district,
        "district_slug": slugify(t.district),
        "district_slot": district_slot(t.district),
        "location": t.location,
        "time_control": t.time_control,
        "time_control_html": time_control_html(t.time_control),
        "organizer": t.organizer,
        "chess_results_url": t.chess_results_url,
        "regulation_url": t.regulation_url,
        "registration_url": t.registration_url,
        "link": t.link,
        "description": t.description,
        "date": t.date.isoformat(),
        "date_fmt": t.date.strftime("%d/%m/%Y"),
        "day": t.date.day,
        "weekday_short": WEEKDAYS_PT[t.date.weekday()],
        "month_short": MONTH_NAMES_PT[t.date.month - 1][:3],
        # Lists are grouped under one heading per month (see _tournament_list.html).
        "month_label": f"{MONTH_NAMES_PT[t.date.month - 1]} {t.date.year}",
        "finished": t.date < today,
        "is_today": t.date == today,
        "share_text": share_text(t, page_url),
    }


def search_tournaments(tournaments: list[Tournament], q: str) -> list[Tournament]:
    q = deaccent(q)
    if not q:
        return tournaments
    fields = ("title", "location", "organizer", "district", "time_control")
    return [t for t in tournaments if any(q in deaccent(getattr(t, f) or "") for f in fields)]


def filter_time_control(tournaments: list[Tournament], ritmo: str) -> list[Tournament]:
    """Only the tournaments in the `ritmo` group; all of them for no group."""
    if ritmo not in TIME_CONTROLS:
        return tournaments
    return [t for t in tournaments if time_control_category(t.time_control) == ritmo]


def split_upcoming_past(tournaments: list[Tournament], today: date) -> tuple[list[dict], list[dict]]:
    upcoming = sorted((t for t in tournaments if t.date >= today), key=lambda t: t.date)
    past = sorted((t for t in tournaments if t.date < today), key=lambda t: t.date, reverse=True)
    return [row(t, today) for t in upcoming], [row(t, today) for t in past]


def build_season_calendar(tournaments: list[Tournament], today: date, anchor: date | None = None) -> list[dict]:
    """Season calendar: 12 months starting with the current (or anchor)
    month, each rendered as its own mini month grid with any approved
    tournaments marked.

    `anchor` picks which month is shown leftmost (defaults to today's own
    month); `today` is only used to mark the "current day" cell, wherever
    it falls. A tournament's own district calendar anchors on that
    tournament's date instead of today, so the tournament itself is
    visible in the grid."""
    start_year = (anchor or today).year
    start_month = (anchor or today).month
    by_date: dict[date, list[Tournament]] = {}
    for t in tournaments:
        by_date.setdefault(t.date, []).append(t)

    cal = _calendar.Calendar(firstweekday=0)
    months = []
    for offset in range(12):
        month = (start_month + offset - 1) % 12 + 1
        year = start_year + (start_month + offset - 1) // 12
        weeks = []
        for week in cal.monthdayscalendar(year, month):
            days = []
            for day_num in week:
                if day_num == 0:
                    days.append(None)
                    continue
                day_date = date(year, month, day_num)
                days.append({
                    "day": day_num,
                    "date": day_date.isoformat(),
                    "is_today": day_date == today,
                    "tournaments": [row(t, today) for t in by_date.get(day_date, [])],
                })
            weeks.append(days)
        months.append({"name": MONTH_NAMES_PT[month - 1], "year": year, "weeks": weeks})
    return months


def districts_nav(names: list[str]) -> list[dict]:
    return [
        {"name": n, "slug": slugify(n), "slot": district_slot(n)}
        for n in sorted(set(names))
    ]


def edit_values(t: Tournament) -> dict:
    """Uniform dict shape for the admin edit form, used both when loading an
    existing tournament and when re-rendering submitted (possibly invalid)
    values after a validation error, so the admin never loses their edits."""
    return {
        "id": t.id,
        "title": t.title,
        "district": t.district,
        "date": t.date.isoformat(),
        "location": t.location,
        "time_control": t.time_control,
        "organizer": t.organizer or "",
        "chess_results_url": t.chess_results_url or "",
        "regulation_url": t.regulation_url or "",
        "registration_url": t.registration_url or "",
        "link": t.link or "",
        "description": t.description or "",
        "status": t.status.value,
        "admin_note": t.admin_note or "",
        "submitted_by_name": t.submitted_by_name,
        "submitted_by_email": t.submitted_by_email,
    }


# --- Corrections suggested by visitors -------------------------------------

# Every field a visitor can correct, in form order, with the label the public
# form uses for it.
FIELD_LABELS = {
    "title": "Nome do torneio",
    "district": "Distrito",
    "date": "Data",
    "location": "Local",
    "time_control": "Ritmo de jogo",
    "organizer": "Organizacao",
    "chess_results_url": "Link chess-results.com",
    "regulation_url": "Link do regulamento",
    "registration_url": "Link de inscricao",
    "link": "Site do torneio",
    "description": "Descricao",
}
MULTILINE_FIELDS = {"description"}


def normalize_value(value) -> str:
    """A stored or submitted value in one comparable shape. Browsers send
    textarea line breaks as CRLF and surrounding whitespace never matters, so
    neither may register as a correction the visitor did not make."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_field(name: str, value) -> str:
    """normalize_value for a tournament field. Browsers drop line breaks from
    single-line inputs, so a stored value containing one (an imported venue,
    say) would otherwise always look edited."""
    value = normalize_value(value)
    if name not in MULTILINE_FIELDS:
        value = value.replace("\n", "").strip()
    return value


def display_value(field: str, value: str, limit: int | None = None) -> str:
    if not value:
        return "-"
    if field == "date":
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except ValueError:
            return value
    if limit and len(value) > limit:
        return value[:limit].rstrip() + "..."
    return value


def _diff_window(old: str, new: str, limit: int) -> tuple[str, str]:
    """Both values cut to the same window around their first difference, so
    a fix near the end of a long text is what shows."""
    if len(old) <= limit and len(new) <= limit:
        return old, new
    first = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
    start = max(0, first - limit // 3)

    def cut(value: str) -> str:
        piece = value[start:start + limit]
        return ("..." if start else "") + piece + ("..." if start + limit < len(value) else "")

    return cut(old), cut(new)


def correction_form_fields(t: Tournament, values: dict, errors: dict, seen: dict | None = None) -> list[dict]:
    """One row per correctable field for the public form. `seen` is what the
    visitor was shown (posted back with the form); it defaults to the
    tournament now. A row starts open when the visitor already changed it or
    it has an error, so a form shown again after a mistake keeps it in view."""
    current = edit_values(t)
    seen = seen or {}
    rows = []
    for name, label in FIELD_LABELS.items():
        base = seen.get(name)
        if base is None:
            base = current[name]
        value = values.get(name, base)
        changed = normalize_field(name, value) != normalize_field(name, base)
        rows.append({
            "name": name,
            "label": label,
            "value": value,
            "seen": base,
            "display": display_value(name, normalize_field(name, base), limit=120),
            "changed": changed,
            "open": changed or name in errors,
        })
    return rows


def correction_changes(c: Correction, t: Tournament) -> list[dict]:
    """The visitor's changes in form order, each with the tournament's value
    now. A change is stale when the tournament no longer has the value the
    visitor saw, i.e. someone edited it after the suggestion was made."""
    current = edit_values(t)
    out = []
    for name, label in FIELD_LABELS.items():
        change = (c.changes or {}).get(name)
        if not change:
            continue
        old, new = change.get("old", ""), change.get("new", "")
        now = normalize_field(name, current[name])
        old_window, new_window = _diff_window(old, new, 80)
        out.append({
            "name": name,
            "label": label,
            "old": old,
            "new": new,
            "old_display": display_value(name, old_window),
            "new_display": display_value(name, new_window),
            "current_display": display_value(name, now),
            "whitespace_only": old != new and old.split() == new.split(),
            "stale": now != old,
        })
    return out


def review_values(t: Tournament, c: Correction) -> dict:
    """Values for the admin edit form when reviewing a correction: the
    tournament as it is now, with a pending suggestion's values on top."""
    values = edit_values(t)
    if c.status == CorrectionStatus.pending:
        for name, change in (c.changes or {}).items():
            if name in FIELD_LABELS:
                values[name] = change.get("new", "")
    return values


def edit_version(t: Tournament) -> str:
    """Fingerprint of the tournament as the edit form loads it. The form
    posts it back, so saving a page opened before another save (a second
    tab, another correction applied meanwhile) is caught instead of quietly
    writing the older values over the newer ones."""
    snapshot = json.dumps(edit_values(t), sort_keys=True, default=str)
    return hashlib.sha256(snapshot.encode()).hexdigest()[:16]
