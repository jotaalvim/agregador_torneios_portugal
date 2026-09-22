import calendar as _calendar
from datetime import date

from .models import Tournament
from .utils import deaccent, slugify

MONTH_NAMES_PT = [
    "JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
]


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
        "location": t.location,
        "time_control": t.time_control,
        "organizer": t.organizer,
        "chess_results_url": t.chess_results_url,
        "regulation_url": t.regulation_url,
        "registration_url": t.registration_url,
        "link": t.link,
        "description": t.description,
        "date": t.date.isoformat(),
        "date_fmt": t.date.strftime("%d/%m/%Y"),
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
    return [{"name": n, "slug": slugify(n)} for n in sorted(set(names))]


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
