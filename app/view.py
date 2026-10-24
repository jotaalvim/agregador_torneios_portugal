import calendar as _calendar
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

from .models import Correction, CorrectionStatus, Tournament
from .utils import deaccent, slugify

MONTH_NAMES_PT = [
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
]
# Indexed by date.weekday() (Monday = 0), for the date tile in the list.
WEEKDAYS_PT = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]
WEEKDAYS_LONG_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# The site's day is mainland Portugal's. The server runs on UTC, which in
# summer is still yesterday for the first hour after midnight in Lisbon.
LISBON = ZoneInfo("Europe/Lisbon")


def today_in_lisbon() -> date:
    return datetime.now(LISBON).date()

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


# The ritmo filter's choices: time controls grouped the way Portuguese chess
# names them, slowest first as in SCRAPING.md's table. "Rápidas" is blitz, not
# rapid, so each chip also shows the usual time control of its group (`hint`).
RITMOS = {
    "classicas": {"label": "Clássicas", "hint": "90+30"},
    "semi-rapidas": {"label": "Semi-rápidas", "hint": "10+5"},
    "rapidas": {"label": "Rápidas", "hint": "3+2"},
}

# The first base + increment in a time control: "90 + 30", or as organizers
# type it, "90' + 30''/lance", "90 min + 30 s". A multi-period "90/40 + 30 + 30"
# reads as 90 + 30, which still puts it among the classical ones.
_BASE_PLUS_INCREMENT = re.compile(r"(\d+)\s*(?:['′’]|m\w*)?\s*(?:/\s*\d+)?\s*\+\s*(\d+)", re.IGNORECASE)


def ritmo_of(time_control: str) -> str | None:
    """The RITMOS key for a time control, by FIDE's limits on a 60-move game:
    base + increment up to 10 minutes is blitz, under 60 rapid, the rest
    classical. None when there is no base + increment to read."""
    m = _BASE_PLUS_INCREMENT.search(time_control)
    if m is None:
        return None
    minutes = int(m[1]) + int(m[2])
    if minutes <= 10:
        return "rapidas"
    if minutes < 60:
        return "semi-rapidas"
    return "classicas"


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


def event_schema(t: Tournament, page_url: str, image_url: str) -> dict | None:
    """The tournament as a schema.org Event, which search engines read to show
    it with its date and venue in their event results. None while the venue is
    not announced (the "Nacional" district, see SCRAPING.md): an event without
    a place is rejected anyway."""
    if slugify(t.district) == "nacional":
        return None
    event = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": t.title,
        "startDate": t.date.isoformat(),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": {
            "@type": "Place",
            "name": t.location,
            "address": {"@type": "PostalAddress", "streetAddress": t.location,
                        "addressRegion": t.district, "addressCountry": "PT"},
        },
        "description": t.description or f"Torneio de xadrez, ritmo {t.time_control}.",
        "image": [image_url],
        "url": page_url,
    }
    if t.organizer:
        event["organizer"] = {"@type": "Organization", "name": t.organizer}
    return event


def ritmo_line(t: Tournament) -> str:
    """The ritmo with its group, for calendar apps: "Ritmo: 90 + 30 (Clássicas)"."""
    group = ritmo_of(t.time_control)
    return f"Ritmo: {t.time_control}" + (f" ({RITMOS[group]['label']})" if group else "")


def calendar_details(t: Tournament, page_url: str) -> str:
    """The notes of the tournament's event in a calendar app: ritmo and
    organizer, the organizer's own description, then the links a player needs
    from there."""
    lines = [ritmo_line(t)]
    if t.organizer:
        lines.append(f"Organização: {t.organizer}")
    if t.description:
        lines += ["", t.description]
    lines.append("")
    if t.registration_url:
        lines.append(f"Inscrição: {t.registration_url}")
    if t.regulation_url:
        lines.append(f"Regulamento: {t.regulation_url}")
    lines.append(f"Página do torneio: {page_url}")
    return "\n".join(lines)


def google_calendar_url(t: Tournament, page_url: str) -> str:
    """Google Calendar's new-event page with the tournament filled in, for
    phones where downloading an .ics opens no calendar (Android, mostly). An
    all-day event, so the end date is the day after. The notes stay short,
    since they travel in the URL: the tournament's page has the rest."""
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE",
        "text": t.title,
        "dates": f"{t.date:%Y%m%d}/{t.date + timedelta(days=1):%Y%m%d}",
        "location": t.location,
        "details": f"{ritmo_line(t)}\n{page_url}",
    })


def countdown(day: date, today: date) -> str:
    """How soon an upcoming tournament is, for the next month or so: "amanhã",
    "daqui a 8 dias". Empty further off, when the date says enough, and from
    the day itself on, when the page says it is on today or has finished."""
    days = (day - today).days
    if days == 1:
        return "amanhã"
    return f"daqui a {days} dias" if 1 < days <= 31 else ""


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
        "ritmo": ritmo_of(t.time_control),
        "organizer": t.organizer,
        "chess_results_url": t.chess_results_url,
        "regulation_url": t.regulation_url,
        "registration_url": t.registration_url,
        "link": t.link,
        "description": t.description,
        "date": t.date.isoformat(),
        "date_fmt": t.date.strftime("%d/%m/%Y"),
        "date_long": f"{WEEKDAYS_LONG_PT[t.date.weekday()].capitalize()}, {t.date.day} de "
                     f"{MONTH_NAMES_PT[t.date.month - 1].lower()} de {t.date.year}",
        "countdown": countdown(t.date, today),
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


def filter_by_ritmo(tournaments: list[Tournament], ritmo: str) -> list[Tournament]:
    if not ritmo:
        return tournaments
    return [t for t in tournaments if ritmo_of(t.time_control) == ritmo]


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


@dataclass(frozen=True)
class Filters:
    """What a list or calendar page is narrowed to: a district slug and a
    RITMOS key, empty for all, plus the list's search. Every link that changes
    the view or a filter is url() with that change, so it keeps the rest."""

    view: str = "list"  # or "calendar"
    district: str = ""
    ritmo: str = ""
    q: str = ""

    def url(self, **changes: str) -> str:
        f = replace(self, **changes)
        if f.view == "calendar":
            return _with_query("/calendario", distrito=f.district, ritmo=f.ritmo)
        return _with_query(f"/distrito/{f.district}" if f.district else "/", q=f.q, ritmo=f.ritmo)

    @property
    def feed_url(self) -> str:
        return _with_query("/calendario.ics", distrito=self.district, ritmo=self.ritmo)

    @property
    def of_ritmo(self) -> str:
        """The ritmo as the empty-list messages put it, " de clássicas", or ""."""
        return f" de {RITMOS[self.ritmo]['label'].lower()}" if self.ritmo else ""


def _with_query(path: str, **params: str) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{query}" if query else path


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
    "organizer": "Organização",
    "chess_results_url": "Link chess-results.com",
    "regulation_url": "Link do regulamento",
    "registration_url": "Link de inscrição",
    "link": "Site do torneio",
    "description": "Descrição",
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
