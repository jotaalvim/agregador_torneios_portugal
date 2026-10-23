import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.middleware.sessions import SessionMiddleware

from . import crud, view
from .auth import (
    check_login_rate_limit,
    clear_login_attempts,
    get_csrf_token,
    is_logged_in,
    register_login_failure,
    require_admin,
    verify_credentials,
    verify_csrf,
)
from .config import GOATCOUNTER_ENDPOINT, GOATCOUNTER_SCRIPT_URL, SECRET_KEY, SESSION_HTTPS_ONLY
from .database import Base, engine, get_db, run_migrations
from .models import Correction, CorrectionStatus, Status, Tournament
from .seed import seed_if_empty
from .utils import ics_escape, is_safe_url

logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migrations()
    db = next(get_db())
    try:
        seed_if_empty(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Torneios Xadrez Portugal", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=SESSION_HTTPS_ONLY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def base_ctx(db: Session, request: Request) -> dict:
    return {
        "request": request,
        "districts": view.districts_nav(crud.get_district_names(db)),
        "goatcounter_script_url": GOATCOUNTER_SCRIPT_URL,
        "goatcounter_endpoint": GOATCOUNTER_ENDPOINT,
    }


FORM_FIELDS = [
    "title",
    "district",
    "date",
    "location",
    "time_control",
    "organizer",
    "chess_results_url",
    "regulation_url",
    "registration_url",
    "link",
    "description",
]

REQUIRED_FIELDS = ["title", "district", "date", "location", "time_control"]
URL_FIELDS = ["chess_results_url", "regulation_url", "registration_url", "link"]

MAX_LENGTHS = {
    "title": 200,
    "district": 100,
    "location": 200,
    "time_control": 150,
    "organizer": 200,
    "chess_results_url": 500,
    "regulation_url": 500,
    "registration_url": 500,
    "link": 500,
    "submitted_by_name": 200,
    "submitted_by_email": 200,
    "description": 5000,
}


def validate_submission(values: dict) -> dict:
    errors = {}
    for field in REQUIRED_FIELDS:
        if not (values.get(field) or "").strip():
            errors[field] = "Campo obrigatorio."

    if values.get("date"):
        try:
            date.fromisoformat(values["date"])
        except ValueError:
            errors["date"] = "Data invalida."

    for field in URL_FIELDS:
        if not is_safe_url(values.get(field) or None):
            errors[field] = "Tem de comecar por http:// ou https://."

    email = (values.get("submitted_by_email") or "").strip()
    if email and "@" not in email:
        errors["submitted_by_email"] = "Email invalido."

    for field, limit in MAX_LENGTHS.items():
        # Browsers send textarea line breaks as CRLF; count each one once, as
        # the public correction form does, or a text that fits there could
        # fail when the admin saves it.
        length = len((values.get(field) or "").replace("\r\n", "\n"))
        if field not in errors and length > limit:
            errors[field] = f"Maximo de {limit} caracteres."

    return errors


# Labels for the error summary at the top of the public correction form.
CORRECTION_LABELS = view.FIELD_LABELS | {
    "note": "Nota",
    "source_url": "Fonte",
    "submitted_by_name": "O teu nome",
    "submitted_by_email": "O teu email",
}

CORRECTION_MAX_LENGTHS = {
    "note": 2000,
    "source_url": 500,
    "submitted_by_name": 200,
    "submitted_by_email": 200,
}


def get_published(db: Session, tournament_id: int) -> Tournament:
    t = crud.get_tournament(db, tournament_id)
    if t is None or t.status != Status.approved:
        raise HTTPException(status_code=404, detail="Torneio nao encontrado")
    return t


def correction_ctx(t: Tournament, c: Correction) -> dict:
    """What the admin edit form needs to show a visitor's correction: the
    suggestion itself and, per changed field, the tournament's value now."""
    changes = view.correction_changes(c, t)
    return {
        "correction": c,
        "correction_pending": c.status == CorrectionStatus.pending,
        "changes": {change["name"]: change for change in changes},
    }


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request) -> str:
    sitemap_url = str(request.url_for("sitemap"))
    return f"User-agent: *\nAllow: /\nDisallow: /admin\n\nSitemap: {sitemap_url}\n"


@app.get("/sitemap.xml", name="sitemap")
def sitemap(request: Request, db: Session = Depends(get_db)) -> Response:
    base = str(request.base_url).rstrip("/")
    urls = [base + "/", base + "/calendario"] + [
        f"{base}/distrito/{d['slug']}" for d in view.districts_nav(crud.get_district_names(db))
    ] + [f"{base}/torneios/{t.id}" for t in crud.list_approved(db)]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    body += [f"<url><loc>{u}</loc></url>" for u in urls]
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), q: str = ""):
    today = date.today()
    tournaments = view.search_tournaments(crud.list_approved(db), q)
    upcoming, past = view.split_upcoming_past(tournaments, today)
    ctx = base_ctx(db, request) | {
        "title": "Torneios",
        "upcoming": upcoming,
        "past": past,
        "q": q,
    }
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/distrito/{slug}", response_class=HTMLResponse)
def district(slug: str, request: Request, db: Session = Depends(get_db), q: str = ""):
    name, tournaments = crud.list_approved_by_district_slug(db, slug)
    if name is None:
        raise HTTPException(status_code=404, detail="Distrito nao encontrado")
    today = date.today()
    tournaments = view.search_tournaments(tournaments, q)
    upcoming, past = view.split_upcoming_past(tournaments, today)
    ctx = base_ctx(db, request) | {
        "district": name,
        "upcoming": upcoming,
        "past": past,
        "q": q,
    }
    return templates.TemplateResponse(request, "district.html", ctx)


@app.get("/calendario", response_class=HTMLResponse)
def calendar_view(request: Request, db: Session = Depends(get_db), distrito: str = ""):
    if distrito:
        name, tournaments = crud.list_approved_by_district_slug(db, distrito)
        if name is None:
            raise HTTPException(status_code=404, detail="Distrito nao encontrado")
    else:
        tournaments = crud.list_approved(db)
    today = date.today()
    upcoming, past = view.split_upcoming_past(tournaments, today)
    ctx = base_ctx(db, request) | {
        "months": view.build_season_calendar(tournaments, today),
        "selected_district": distrito,
        "upcoming": upcoming,
        "past": past,
    }
    return templates.TemplateResponse(request, "calendar.html", ctx)


@app.get("/torneios/{tournament_id}", response_class=HTMLResponse)
def tournament_detail(tournament_id: int, request: Request, db: Session = Depends(get_db)):
    t = crud.get_tournament(db, tournament_id)
    if t is None or t.status != Status.approved:
        raise HTTPException(status_code=404, detail="Torneio nao encontrado")
    today = date.today()
    row_ctx = view.row(t, today, page_url=str(request.url))
    _, district_tournaments = crud.list_approved_by_district_slug(db, row_ctx["district_slug"])
    ctx = base_ctx(db, request) | {
        "t": row_ctx,
        "months": view.build_season_calendar(district_tournaments, today, anchor=t.date),
    }
    return templates.TemplateResponse(request, "tournament.html", ctx)


@app.get("/torneios/{tournament_id}/ics")
def tournament_ics(tournament_id: int, db: Session = Depends(get_db)):
    t = crud.get_tournament(db, tournament_id)
    if t is None or t.status != Status.approved:
        raise HTTPException(status_code=404, detail="Torneio nao encontrado")
    body = render_ics([t])
    return PlainTextResponse(body, media_type="text/calendar")


@app.get("/calendario.ics")
def calendar_feed(db: Session = Depends(get_db), distrito: str = ""):
    if distrito:
        name, tournaments = crud.list_approved_by_district_slug(db, distrito)
        if name is None:
            raise HTTPException(status_code=404, detail="Distrito nao encontrado")
    else:
        tournaments = crud.list_approved(db)
    body = render_ics(tournaments)
    return PlainTextResponse(body, media_type="text/calendar")


def render_ics(tournaments: list[Tournament]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Xadrez Distrito de Braga//PT"]
    for t in tournaments:
        lines += [
            "BEGIN:VEVENT",
            f"UID:tournament-{t.id}@xadreznorte",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{t.date.strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(t.title)}",
            f"LOCATION:{ics_escape(t.location)}",
        ]
        if t.description:
            lines.append(f"DESCRIPTION:{ics_escape(t.description)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@app.get("/submeter", response_class=HTMLResponse)
def submit_form(request: Request, db: Session = Depends(get_db)):
    ctx = base_ctx(db, request) | {"errors": {}, "values": {}}
    return templates.TemplateResponse(request, "submit.html", ctx)


@app.post("/submeter", response_class=HTMLResponse)
def submit_form_post(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(""),
    district: str = Form(""),
    date_: str = Form("", alias="date"),
    location: str = Form(""),
    time_control: str = Form(""),
    organizer: str = Form(""),
    chess_results_url: str = Form(""),
    regulation_url: str = Form(""),
    registration_url: str = Form(""),
    link: str = Form(""),
    description: str = Form(""),
    submitted_by_name: str = Form(""),
    submitted_by_email: str = Form(""),
    confirmacao: str = Form(""),  # honeypot: real users never fill this in
):
    values = {
        "title": title.strip(),
        "district": district.strip(),
        "date": date_.strip(),
        "location": location.strip(),
        "time_control": time_control.strip(),
        "organizer": organizer.strip(),
        "chess_results_url": chess_results_url.strip(),
        "regulation_url": regulation_url.strip(),
        "registration_url": registration_url.strip(),
        "link": link.strip(),
        "description": description.strip(),
        "submitted_by_name": submitted_by_name.strip(),
        "submitted_by_email": submitted_by_email.strip(),
    }

    if confirmacao.strip():
        # Looks like a bot. Pretend success without writing anything, but
        # log it in case a real visitor's browser/extension autofilled the
        # hidden field so a false positive can be spotted and investigated.
        logger.warning("Submissao bloqueada pelo honeypot (possivel falso positivo): %r", values)
        return RedirectResponse("/submeter/obrigado", status_code=303)

    errors = validate_submission(values)
    if errors:
        ctx = base_ctx(db, request) | {"errors": errors, "values": values}
        return templates.TemplateResponse(request, "submit.html", ctx, status_code=400)

    data = {
        "title": values["title"],
        "district": values["district"],
        "date": date.fromisoformat(values["date"]),
        "location": values["location"],
        "time_control": values["time_control"],
        "organizer": values["organizer"] or None,
        "chess_results_url": values["chess_results_url"] or None,
        "regulation_url": values["regulation_url"] or None,
        "registration_url": values["registration_url"] or None,
        "link": values["link"] or None,
        "description": values["description"] or None,
        "submitted_by_name": values["submitted_by_name"] or None,
        "submitted_by_email": values["submitted_by_email"] or None,
    }
    try:
        crud.create_tournament(db, data, status=Status.pending)
    except Exception:
        logger.exception("Falha ao gravar submissao")
        db.rollback()
        ctx = base_ctx(db, request) | {
            "errors": {"general": "Nao foi possivel gravar a submissao. Tenta novamente."},
            "values": values,
        }
        return templates.TemplateResponse(request, "submit.html", ctx, status_code=500)
    return RedirectResponse("/submeter/obrigado", status_code=303)


@app.get("/submeter/obrigado", response_class=HTMLResponse)
def submit_thanks(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "submit_success.html", base_ctx(db, request))


# --- Corrections from visitors -------------------------------------------


@app.get("/torneios/{tournament_id}/corrigir", response_class=HTMLResponse)
def correction_form(tournament_id: int, request: Request, db: Session = Depends(get_db)):
    t = get_published(db, tournament_id)
    ctx = base_ctx(db, request) | {
        "t": view.row(t, date.today()),
        "fields": view.correction_form_fields(t, {}, {}),
        "values": {},
        "errors": {},
    }
    return templates.TemplateResponse(request, "correct.html", ctx)


async def read_form(request: Request) -> FormData:
    """The raw posted form. FastAPI turns an empty form value into the
    parameter's default, which would make clearing a field look the same as
    not sending it; the correction form needs to tell those apart."""
    return await request.form()


@app.post("/torneios/{tournament_id}/corrigir", response_class=HTMLResponse)
def correction_post(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    form: FormData = Depends(read_form),
):
    t = get_published(db, tournament_id)

    def text(name: str) -> str | None:
        value = form.get(name)
        return value if isinstance(value, str) else None

    # None: the field was not sent at all, which is never a change.
    # "": the visitor cleared it.
    submitted = {name: text(name) for name in view.FIELD_LABELS}
    extra = {
        "cancelled": bool((text("cancelled") or "").strip()),
        "note": view.normalize_value(text("note")),
        "source_url": (text("source_url") or "").strip(),
        "submitted_by_name": (text("submitted_by_name") or "").strip(),
        "submitted_by_email": (text("submitted_by_email") or "").strip(),
    }
    confirmacao = text("confirmacao") or ""  # honeypot, as on /submeter

    if confirmacao.strip():
        logger.warning("Correcao bloqueada pelo honeypot (possivel falso positivo): %r", extra)
        return RedirectResponse(f"/torneios/{t.id}/corrigir/obrigado", status_code=303)

    # Compare with what the visitor was shown (posted back as seen_<field>),
    # not with the tournament now: if it was edited while the form was open,
    # their untouched fields must not turn into changes reverting that edit.
    # Keeping the seen value as "old" lets the review flag such fields.
    current = view.edit_values(t)
    seen, changes = {}, {}
    for name in view.FIELD_LABELS:
        posted_seen = text(f"seen_{name}")
        seen[name] = posted_seen if posted_seen is not None else current[name]
        if submitted[name] is None:
            continue
        old = view.normalize_field(name, seen[name])
        new = view.normalize_field(name, submitted[name])
        if new != old:
            changes[name] = {"old": old, "new": new}

    # Validate the tournament as it would be with the changes applied, but
    # only report fields the visitor touched: an old bad value elsewhere is
    # not theirs to fix.
    merged = {name: current[name] for name in view.FIELD_LABELS} | {
        name: change["new"] for name, change in changes.items()
    }
    errors = {field: msg for field, msg in validate_submission(merged).items() if field in changes}
    if not is_safe_url(extra["source_url"] or None):
        errors["source_url"] = "Tem de comecar por http:// ou https://."
    if extra["submitted_by_email"] and "@" not in extra["submitted_by_email"]:
        errors["submitted_by_email"] = "Email invalido."
    for field, limit in CORRECTION_MAX_LENGTHS.items():
        if field not in errors and len(extra[field]) > limit:
            errors[field] = f"Maximo de {limit} caracteres."
    if not changes and not extra["cancelled"] and not extra["note"]:
        errors["general"] = "Corrige pelo menos um campo ou escreve uma nota."
    elif errors:
        # The fields in error can be screens below the top of a long form.
        labels = [label for field, label in CORRECTION_LABELS.items() if field in errors]
        errors["general"] = "Ha campos por rever mais abaixo: " + ", ".join(labels) + "."

    if errors:
        values = {
            name: (submitted[name] if submitted[name] is not None else seen[name])
            for name in view.FIELD_LABELS
        } | extra
        ctx = base_ctx(db, request) | {
            "t": view.row(t, date.today()),
            "fields": view.correction_form_fields(t, values, errors, seen),
            "values": values,
            "errors": errors,
        }
        return templates.TemplateResponse(request, "correct.html", ctx, status_code=400)

    crud.create_correction(db, {
        "tournament_id": t.id,
        "changes": changes,
        "cancelled": extra["cancelled"],
        "note": extra["note"] or None,
        "source_url": extra["source_url"] or None,
        "submitted_by_name": extra["submitted_by_name"] or None,
        "submitted_by_email": extra["submitted_by_email"] or None,
    })
    return RedirectResponse(f"/torneios/{t.id}/corrigir/obrigado", status_code=303)


@app.get("/torneios/{tournament_id}/corrigir/obrigado", response_class=HTMLResponse)
def correction_thanks(tournament_id: int, request: Request, db: Session = Depends(get_db)):
    t = get_published(db, tournament_id)
    ctx = base_ctx(db, request) | {"t": view.row(t, date.today())}
    return templates.TemplateResponse(request, "correct_success.html", ctx)


# --- Admin -------------------------------------------------------------


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request, db: Session = Depends(get_db)):
    if is_logged_in(request):
        return RedirectResponse("/admin", status_code=303)
    ctx = base_ctx(db, request) | {"error": None}
    return templates.TemplateResponse(request, "admin/login.html", ctx)


@app.post("/admin/login", response_class=HTMLResponse)
def admin_login_post(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    if not check_login_rate_limit(request):
        ctx = base_ctx(db, request) | {"error": "Demasiadas tentativas. Tenta novamente mais tarde."}
        return templates.TemplateResponse(request, "admin/login.html", ctx, status_code=429)

    if verify_credentials(username, password):
        clear_login_attempts(request)
        request.session["admin"] = True
        return RedirectResponse("/admin", status_code=303)

    register_login_failure(request)
    ctx = base_ctx(db, request) | {"error": "Credenciais invalidas."}
    return templates.TemplateResponse(request, "admin/login.html", ctx, status_code=400)


@app.post("/admin/logout", dependencies=[Depends(require_admin)])
def admin_logout(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/admin/tournaments/new", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_new_form(request: Request, db: Session = Depends(get_db)):
    ctx = base_ctx(db, request) | {
        "values": {"status": Status.approved.value},
        "errors": {},
        "csrf_token": get_csrf_token(request),
    }
    return templates.TemplateResponse(request, "admin/new.html", ctx)


@app.post("/admin/tournaments/new", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_new_post(
    request: Request,
    db: Session = Depends(get_db),
    csrf_token: str = Form(...),
    title: str = Form(""),
    district: str = Form(""),
    date_: str = Form("", alias="date"),
    location: str = Form(""),
    time_control: str = Form(""),
    organizer: str = Form(""),
    chess_results_url: str = Form(""),
    regulation_url: str = Form(""),
    registration_url: str = Form(""),
    link: str = Form(""),
    description: str = Form(""),
    status: str = Form(Status.approved.value),
    admin_note: str = Form(""),
):
    verify_csrf(request, csrf_token)

    values = {
        "title": title.strip(),
        "district": district.strip(),
        "date": date_.strip(),
        "location": location.strip(),
        "time_control": time_control.strip(),
        "organizer": organizer.strip(),
        "chess_results_url": chess_results_url.strip(),
        "regulation_url": regulation_url.strip(),
        "registration_url": registration_url.strip(),
        "link": link.strip(),
        "description": description.strip(),
        "status": status,
        "admin_note": admin_note.strip(),
    }
    errors = validate_submission(values)
    if status not in (Status.pending, Status.approved, Status.rejected):
        errors["status"] = "Estado invalido."

    if errors:
        ctx = base_ctx(db, request) | {"values": values, "errors": errors, "csrf_token": get_csrf_token(request)}
        return templates.TemplateResponse(request, "admin/new.html", ctx, status_code=400)

    data = {
        "title": values["title"],
        "district": values["district"],
        "date": date.fromisoformat(values["date"]),
        "location": values["location"],
        "time_control": values["time_control"],
        "organizer": values["organizer"] or None,
        "chess_results_url": values["chess_results_url"] or None,
        "regulation_url": values["regulation_url"] or None,
        "registration_url": values["registration_url"] or None,
        "link": values["link"] or None,
        "description": values["description"] or None,
        "admin_note": values["admin_note"] or None,
    }
    crud.create_tournament(db, data, status=Status(status))
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    corrections = [
        {"c": c, "t": c.tournament, "changes": view.correction_changes(c, c.tournament)}
        for c in crud.list_pending_corrections(db)
    ]
    ctx = base_ctx(db, request) | {
        "pending": crud.list_pending(db),
        "corrections": corrections,
        "all_tournaments": crud.list_all(db),
        "csrf_token": get_csrf_token(request),
    }
    return templates.TemplateResponse(request, "admin/dashboard.html", ctx)


@app.post("/admin/tournaments/{tournament_id}/approve", dependencies=[Depends(require_admin)])
def admin_approve(tournament_id: int, request: Request, db: Session = Depends(get_db), csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    t = crud.get_tournament(db, tournament_id)
    if t is None:
        raise HTTPException(status_code=404)
    crud.set_status(db, t, Status.approved)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/tournaments/{tournament_id}/reject", dependencies=[Depends(require_admin)])
def admin_reject(tournament_id: int, request: Request, db: Session = Depends(get_db), csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    t = crud.get_tournament(db, tournament_id)
    if t is None:
        raise HTTPException(status_code=404)
    crud.set_status(db, t, Status.rejected)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/tournaments/{tournament_id}/delete", dependencies=[Depends(require_admin)])
def admin_delete(tournament_id: int, request: Request, db: Session = Depends(get_db), csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    t = crud.get_tournament(db, tournament_id)
    if t is None:
        raise HTTPException(status_code=404)
    crud.delete_tournament(db, t)
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/tournaments/{tournament_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_edit_form(tournament_id: int, request: Request, db: Session = Depends(get_db)):
    t = crud.get_tournament(db, tournament_id)
    if t is None:
        raise HTTPException(status_code=404)
    ctx = base_ctx(db, request) | {
        "values": view.edit_values(t),
        "errors": {},
        "csrf_token": get_csrf_token(request),
        "version": view.edit_version(t),
    }
    return templates.TemplateResponse(request, "admin/edit.html", ctx)


@app.post("/admin/tournaments/{tournament_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_edit_post(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    csrf_token: str = Form(...),
    title: str = Form(""),
    district: str = Form(""),
    date_: str = Form("", alias="date"),
    location: str = Form(""),
    time_control: str = Form(""),
    organizer: str = Form(""),
    chess_results_url: str = Form(""),
    regulation_url: str = Form(""),
    registration_url: str = Form(""),
    link: str = Form(""),
    description: str = Form(""),
    status: str = Form(...),
    admin_note: str = Form(""),
    correction_id: str = Form(""),  # set when this edit reviews a visitor's correction
    version: str = Form(""),  # view.edit_version when the form was loaded; scripts may omit it
):
    verify_csrf(request, csrf_token)
    t = crud.get_tournament(db, tournament_id)
    if t is None:
        raise HTTPException(status_code=404)
    correction = crud.get_correction(db, int(correction_id)) if correction_id.strip().isdigit() else None
    if correction is not None and (correction.tournament_id != t.id or not crud.correction_belongs(correction)):
        correction = None

    if version and version != view.edit_version(t):
        # Saved from a page loaded before another save. Writing it would put
        # back the older values of every field, so reload the current
        # tournament (with the correction's suggestion on top, if any).
        pending = correction is not None and correction.status == CorrectionStatus.pending
        ctx = base_ctx(db, request) | {
            "values": view.review_values(t, correction) if pending else view.edit_values(t),
            "errors": {"general": "Este torneio foi alterado noutra janela depois de abrires esta pagina. "
                                  "O formulario foi recarregado com a versao atual: confere e guarda outra vez."},
            "csrf_token": get_csrf_token(request),
            "version": view.edit_version(t),
        }
        if correction is not None:
            ctx |= correction_ctx(t, correction)
        return templates.TemplateResponse(request, "admin/edit.html", ctx, status_code=409)

    values = {
        "id": t.id,
        "title": title.strip(),
        "district": district.strip(),
        "date": date_.strip(),
        "location": location.strip(),
        "time_control": time_control.strip(),
        "organizer": organizer.strip(),
        "chess_results_url": chess_results_url.strip(),
        "regulation_url": regulation_url.strip(),
        "registration_url": registration_url.strip(),
        "link": link.strip(),
        "description": description.strip(),
        "status": status,
        "admin_note": admin_note.strip(),
        "submitted_by_name": t.submitted_by_name,
        "submitted_by_email": t.submitted_by_email,
    }
    errors = validate_submission(values)
    if status not in (Status.pending, Status.approved, Status.rejected):
        errors["status"] = "Estado invalido."

    if errors:
        # Re-render with what the admin just typed (not the stale DB record),
        # so a single bad field doesn't wipe out the rest of the edit.
        ctx = base_ctx(db, request) | {
            "values": values,
            "errors": errors,
            "csrf_token": get_csrf_token(request),
            "version": version,
        }
        if correction is not None:
            ctx |= correction_ctx(t, correction)
        return templates.TemplateResponse(request, "admin/edit.html", ctx, status_code=400)

    data = {
        "title": values["title"],
        "district": values["district"],
        "date": date.fromisoformat(values["date"]),
        "location": values["location"],
        "time_control": values["time_control"],
        "organizer": organizer.strip() or None,
        "chess_results_url": values["chess_results_url"] or None,
        "regulation_url": values["regulation_url"] or None,
        "registration_url": values["registration_url"] or None,
        "link": values["link"] or None,
        "description": description.strip() or None,
        "status": Status(status),
        "admin_note": admin_note.strip() or None,
    }
    crud.update_tournament(db, t, data)
    if correction is not None and correction.status == CorrectionStatus.pending:
        crud.set_correction_status(db, correction, CorrectionStatus.resolved)
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/correcoes/{correction_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_correction_review(correction_id: int, request: Request, db: Session = Depends(get_db)):
    """The normal edit form, with the visitor's suggested values filled in.
    Saving it applies whatever the admin keeps and closes the correction."""
    c = crud.get_correction(db, correction_id)
    if c is None or not crud.correction_belongs(c):
        raise HTTPException(status_code=404)
    ctx = base_ctx(db, request) | {
        "values": view.review_values(c.tournament, c),
        "errors": {},
        "csrf_token": get_csrf_token(request),
        "version": view.edit_version(c.tournament),
    } | correction_ctx(c.tournament, c)
    return templates.TemplateResponse(request, "admin/edit.html", ctx)


def _close_correction(db: Session, request: Request, correction_id: int, csrf_token: str, status: CorrectionStatus):
    verify_csrf(request, csrf_token)
    c = crud.get_correction(db, correction_id)
    if c is None:
        raise HTTPException(status_code=404)
    if c.status == CorrectionStatus.pending:
        crud.set_correction_status(db, c, status)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/correcoes/{correction_id}/resolve", dependencies=[Depends(require_admin)])
def admin_correction_resolve(
    correction_id: int, request: Request, db: Session = Depends(get_db), csrf_token: str = Form(...)
):
    """Handled without saving the form, e.g. a note acted on elsewhere."""
    return _close_correction(db, request, correction_id, csrf_token, CorrectionStatus.resolved)


@app.post("/admin/correcoes/{correction_id}/reject", dependencies=[Depends(require_admin)])
def admin_correction_reject(
    correction_id: int, request: Request, db: Session = Depends(get_db), csrf_token: str = Form(...)
):
    return _close_correction(db, request, correction_id, csrf_token, CorrectionStatus.rejected)
