"""Visitor corrections: the public form on each tournament page, and the
admin review that applies them through the normal edit form."""

import re
from datetime import date
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal
from app.main import app
from app.models import Correction, CorrectionStatus, Status, Tournament

ADMIN = {"username": "testadmin", "password": "testpass"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def login(client: TestClient) -> None:
    r = client.post("/admin/login", data=ADMIN, follow_redirects=False)
    assert r.status_code == 303


def make_tournament(**overrides) -> int:
    data = {
        "title": "Open de Testes das Correcoes",
        "district": "Braga",
        "date": date(2027, 5, 1),
        "location": "Sala de Testes, Braga",
        "time_control": "90 + 30",
        "organizer": "Clube de Testes",
        "description": "Linha um\nLinha dois",
        "status": Status.approved,
    } | overrides
    with SessionLocal() as db:
        t = Tournament(**data)
        db.add(t)
        db.commit()
        return t.id


def get_tournament(tid: int) -> Tournament:
    with SessionLocal() as db:
        return db.get(Tournament, tid)


def update_tournament(tid: int, **fields) -> None:
    with SessionLocal() as db:
        t = db.get(Tournament, tid)
        for k, v in fields.items():
            setattr(t, k, v)
        db.commit()


def corrections_for(tid: int) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(Correction).where(Correction.tournament_id == tid).order_by(Correction.id))
        return [
            {"id": c.id, "changes": c.changes, "note": c.note, "cancelled": c.cancelled,
             "source_url": c.source_url, "status": c.status}
            for c in rows
        ]


class FormValues(HTMLParser):
    """The values a browser would submit for the forms on a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}
        self._textarea = None
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag == "input" and name and a.get("type") != "checkbox":
            value = a.get("value") or ""
            if a.get("type") != "hidden":
                # Browsers drop line breaks from single-line inputs.
                value = value.replace("\r", "").replace("\n", "")
            self.values.setdefault(name, value)
        elif tag == "textarea" and name:
            self._textarea = name
            self.values[name] = ""
        elif tag == "select" and name:
            self._select = name
        elif tag == "option" and self._select and "selected" in a:
            self.values[self._select] = a.get("value") or ""

    def handle_data(self, data):
        if self._textarea:
            self.values[self._textarea] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self._textarea = None
        elif tag == "select":
            self._select = None


def form_values(page: str) -> dict:
    parser = FormValues()
    parser.feed(page)
    # Browsers submit every line break in a form as CRLF.
    return {k: v.replace("\r\n", "\n").replace("\n", "\r\n") for k, v in parser.values.items()}


def browser_form(client: TestClient, tid: int) -> dict:
    """The correction form exactly as a visitor who changes nothing sends it."""
    return form_values(client.get(f"/torneios/{tid}/corrigir").text)


def suggest(client: TestClient, tid: int, **changes) -> None:
    r = client.post(f"/torneios/{tid}/corrigir", data=browser_form(client, tid) | changes, follow_redirects=False)
    assert r.status_code == 303, r.text


def dashboard_row(client: TestClient, correction_id: int) -> str:
    """The dashboard table row of one correction, so assertions cannot be
    satisfied by another test's leftovers in the shared database."""
    page = client.get("/admin").text
    for row in re.findall(r"<tr>.*?</tr>", page, flags=re.S):
        if f'href="/admin/correcoes/{correction_id}"' in row:
            return row
    raise AssertionError(f"correction {correction_id} is not on the dashboard")


def admin_csrf(client: TestClient) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin").text)
    assert match
    return match.group(1)


# --- public form -------------------------------------------------------------


def test_tournament_page_links_to_the_correction_form(client):
    tid = make_tournament()
    assert f'href="/torneios/{tid}/corrigir"' in client.get(f"/torneios/{tid}").text


def test_correction_form_shows_current_values(client):
    tid = make_tournament(location="Pavilhao Unico, Barcelos")
    r = client.get(f"/torneios/{tid}/corrigir")
    assert r.status_code == 200
    assert '<span class="fix-current">Pavilhao Unico, Barcelos</span>' in r.text
    assert 'value="Pavilhao Unico, Barcelos"' in r.text
    assert '<span class="fix-current">01/05/2027</span>' in r.text


def test_correction_form_only_exists_for_published_tournaments(client):
    # Rejected rather than pending: unpublished, but kept out of the
    # "Pendentes" table that test_app.py reads.
    tid = make_tournament(status=Status.rejected)
    assert client.get(f"/torneios/{tid}/corrigir").status_code == 404
    assert client.post(f"/torneios/{tid}/corrigir", data={"note": "x"}).status_code == 404
    assert client.get(f"/torneios/{tid}/corrigir/obrigado").status_code == 404
    assert client.get("/torneios/999999/corrigir").status_code == 404


def test_untouched_form_is_not_a_correction(client):
    # The description holds line breaks, which come back as CRLF.
    tid = make_tournament()
    r = client.post(f"/torneios/{tid}/corrigir", data=browser_form(client, tid))
    assert r.status_code == 400
    assert "Corrige pelo menos um campo ou escreve uma nota." in r.text
    assert corrections_for(tid) == []


def test_changed_field_is_stored_alone_and_not_published(client):
    tid = make_tournament(location="Sala Antiga, Braga")
    form = browser_form(client, tid) | {"location": "  Sala Nova, Braga ", "source_url": "https://example.org/reg.pdf"}
    r = client.post(f"/torneios/{tid}/corrigir", data=form, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/torneios/{tid}/corrigir/obrigado"
    assert "Obrigado" in client.get(r.headers["location"]).text

    [c] = corrections_for(tid)
    assert c["changes"] == {"location": {"old": "Sala Antiga, Braga", "new": "Sala Nova, Braga"}}
    assert c["source_url"] == "https://example.org/reg.pdf"
    assert c["status"] == CorrectionStatus.pending

    page = client.get(f"/torneios/{tid}").text
    assert "Sala Antiga, Braga" in page
    assert "Sala Nova, Braga" not in page


def test_clearing_an_optional_field_is_a_change(client):
    tid = make_tournament(organizer="Clube Errado")
    suggest(client, tid, organizer="")
    [c] = corrections_for(tid)
    assert c["changes"] == {"organizer": {"old": "Clube Errado", "new": ""}}


def test_a_note_or_the_cancelled_box_is_enough(client):
    tid = make_tournament()
    # Fields left out of the post entirely are never treated as cleared.
    r = client.post(f"/torneios/{tid}/corrigir", data={"note": "Inscricoes esgotadas.\r\nSem lista de espera."},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/torneios/{tid}/corrigir", data={"cancelled": "1"}, follow_redirects=False)
    assert r.status_code == 303

    note_only, cancelled = corrections_for(tid)
    assert note_only["changes"] == {}
    assert note_only["note"] == "Inscricoes esgotadas.\nSem lista de espera."
    assert not note_only["cancelled"]
    assert cancelled["changes"] == {}
    assert cancelled["cancelled"]
    assert cancelled["note"] is None


@pytest.mark.parametrize("field,value,message", [
    ("title", "", "Campo obrigatório."),
    ("date", "31/10/2026", "Data inválida."),
    ("regulation_url", "javascript:alert(1)", "Tem de começar por http:// ou https://."),
    ("source_url", "ftp://example.org/x", "Tem de começar por http:// ou https://."),
    ("submitted_by_email", "nao-e-um-email", "Email inválido."),
    ("note", "x" * 2001, "Máximo de 2000 caracteres."),
])
def test_invalid_input_is_refused_and_kept(client, field, value, message):
    tid = make_tournament()
    form = browser_form(client, tid) | {"note": "uma nota"} | {field: value}
    r = client.post(f"/torneios/{tid}/corrigir", data=form)
    assert r.status_code == 400
    assert message in r.text
    if value and field != "note":
        assert f'value="{value}"' in r.text
    assert corrections_for(tid) == []


def test_refused_form_reopens_the_rows_being_fixed(client):
    tid = make_tournament()
    r = client.post(f"/torneios/{tid}/corrigir", data=browser_form(client, tid) | {"date": "31/10/2026"})
    assert r.status_code == 400
    assert re.search(r'<details class="fix-row is-changed" open>\s*<summary>\s*<span class="fix-label">Data</span>', r.text)
    assert re.search(r'<details class="fix-row">\s*<summary>\s*<span class="fix-label">Local</span>', r.text)


def test_honeypot_is_silently_dropped(client):
    tid = make_tournament()
    r = client.post(f"/torneios/{tid}/corrigir", data={"note": "bot", "confirmacao": "x"}, follow_redirects=False)
    assert r.status_code == 303
    assert corrections_for(tid) == []


# --- admin review ------------------------------------------------------------


def test_dashboard_lists_pending_corrections(client):
    tid = make_tournament(title="Open do Painel de Correcoes", time_control="90 + 30")
    suggest(client, tid, time_control="60 + 30", note="Ver o regulamento novo.", cancelled="1")
    [c] = corrections_for(tid)
    login(client)
    row = dashboard_row(client, c["id"])
    assert "Open do Painel de Correcoes" in row
    assert "<del>90 + 30</del>" in row
    assert "<ins>60 + 30</ins>" in row
    assert "Ver o regulamento novo." in row
    assert "Cancelado ou adiado" in row


def test_review_prefills_the_suggestion_and_saving_applies_it(client):
    tid = make_tournament(location="Sala Antiga, Braga")
    suggest(client, tid, location="Sala Nova, Braga")
    [c] = corrections_for(tid)

    login(client)
    review = client.get(f"/admin/correcoes/{c['id']}").text
    assert 'value="Sala Nova, Braga"' in review
    assert "Valor atual: Sala Antiga, Braga" in review
    assert f'name="correction_id" value="{c["id"]}"' in review

    r = client.post(f"/admin/tournaments/{tid}/edit", data=form_values(review), follow_redirects=False)
    assert r.status_code == 303

    t = get_tournament(tid)
    assert t.location == "Sala Nova, Braga"
    assert t.status == Status.approved
    # The edit form saves what the browser sends, CRLF included, as before.
    assert t.description.replace("\r\n", "\n") == "Linha um\nLinha dois"
    assert corrections_for(tid)[0]["status"] == CorrectionStatus.resolved
    assert "Sala Nova, Braga" in client.get(f"/torneios/{tid}").text
    assert f"/admin/correcoes/{c['id']}" not in client.get("/admin").text


def test_admin_can_keep_part_of_a_suggestion(client):
    tid = make_tournament(location="Sala Antiga, Braga", time_control="90 + 30")
    suggest(client, tid, location="Sala Nova, Braga", time_control="5 + 3")
    [c] = corrections_for(tid)
    login(client)
    form = form_values(client.get(f"/admin/correcoes/{c['id']}").text) | {"time_control": "90 + 30"}
    assert client.post(f"/admin/tournaments/{tid}/edit", data=form, follow_redirects=False).status_code == 303
    t = get_tournament(tid)
    assert (t.location, t.time_control) == ("Sala Nova, Braga", "90 + 30")


def test_reject_and_mark_handled_leave_the_tournament_alone(client):
    tid = make_tournament(location="Sala Antiga, Braga")
    suggest(client, tid, location="Sala Errada, Braga")
    suggest(client, tid, note="Mudou a hora.")
    wrong, note = corrections_for(tid)

    login(client)
    token = admin_csrf(client)
    assert client.post(f"/admin/correcoes/{wrong['id']}/reject", data={"csrf_token": token},
                       follow_redirects=False).status_code == 303
    assert client.post(f"/admin/correcoes/{note['id']}/resolve", data={"csrf_token": token},
                       follow_redirects=False).status_code == 303

    wrong, note = corrections_for(tid)
    assert wrong["status"] == CorrectionStatus.rejected
    assert note["status"] == CorrectionStatus.resolved
    assert get_tournament(tid).location == "Sala Antiga, Braga"

    handled = client.get(f"/admin/correcoes/{wrong['id']}").text
    assert "já foi tratada" in handled
    assert 'name="correction_id"' not in handled
    assert 'value="Sala Antiga, Braga"' in handled


def test_review_flags_fields_edited_after_the_suggestion(client):
    tid = make_tournament(location="Sala Antiga, Braga")
    suggest(client, tid, location="Sala Nova, Braga")
    [c] = corrections_for(tid)
    update_tournament(tid, location="Sala Intermedia, Braga")

    login(client)
    review = client.get(f"/admin/correcoes/{c['id']}").text
    assert "Valor atual: Sala Intermedia, Braga" in review
    assert "Este campo mudou depois da sugestão. Quando foi enviada, o valor era: Sala Antiga, Braga" in review
    assert "(mudou depois)" in dashboard_row(client, c["id"])


def test_failed_save_keeps_the_review(client):
    tid = make_tournament(location="Sala Antiga, Braga")
    suggest(client, tid, location="Sala Nova, Braga")
    [c] = corrections_for(tid)
    login(client)
    form = form_values(client.get(f"/admin/correcoes/{c['id']}").text) | {"title": ""}
    r = client.post(f"/admin/tournaments/{tid}/edit", data=form)
    assert r.status_code == 400
    assert f'name="correction_id" value="{c["id"]}"' in r.text
    assert "Valor atual: Sala Antiga, Braga" in r.text
    assert corrections_for(tid)[0]["status"] == CorrectionStatus.pending


def test_correction_for_another_tournament_is_not_closed(client):
    a = make_tournament(location="Sala A, Braga")
    b = make_tournament(location="Sala B, Braga")
    suggest(client, a, location="Sala A2, Braga")
    [c] = corrections_for(a)
    login(client)
    form = form_values(client.get(f"/admin/tournaments/{b}/edit").text) | {"correction_id": str(c["id"])}
    assert client.post(f"/admin/tournaments/{b}/edit", data=form, follow_redirects=False).status_code == 303
    assert corrections_for(a)[0]["status"] == CorrectionStatus.pending
    assert get_tournament(a).location == "Sala A, Braga"


def test_admin_correction_routes_need_login_and_csrf(client):
    tid = make_tournament()
    suggest(client, tid, note="Nota de teste.")
    [c] = corrections_for(tid)

    r = client.get(f"/admin/correcoes/{c['id']}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"
    r = client.post(f"/admin/correcoes/{c['id']}/reject", data={"csrf_token": "x"}, follow_redirects=False)
    assert r.status_code == 303

    login(client)
    assert client.post(f"/admin/correcoes/{c['id']}/reject", data={"csrf_token": "bogus"}).status_code == 400
    assert corrections_for(tid)[0]["status"] == CorrectionStatus.pending
    assert client.get("/admin/correcoes/999999").status_code == 404


def test_deleting_a_tournament_removes_its_corrections(client):
    tid = make_tournament()
    suggest(client, tid, note="Vai ser apagado.")
    login(client)
    r = client.post(f"/admin/tournaments/{tid}/delete", data={"csrf_token": admin_csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    assert corrections_for(tid) == []
    assert client.get("/admin").status_code == 200


# --- what the visitor saw ------------------------------------------------------


def test_edit_made_while_the_form_was_open_is_not_a_revert(client):
    tid = make_tournament(location="Sala Antiga, Braga", time_control="90 + 30")
    form = browser_form(client, tid)
    update_tournament(tid, location="Sala Corrigida, Braga")  # the admin fixes it meanwhile

    r = client.post(f"/torneios/{tid}/corrigir", data=form | {"time_control": "60 + 30"}, follow_redirects=False)
    assert r.status_code == 303
    [c] = corrections_for(tid)
    assert c["changes"] == {"time_control": {"old": "90 + 30", "new": "60 + 30"}}

    # an untouched form is still nothing to send
    r = client.post(f"/torneios/{tid}/corrigir", data=form)
    assert r.status_code == 400


def test_suggestion_based_on_an_outdated_value_is_flagged(client):
    tid = make_tournament(location="Sala Antiga, Braga")
    form = browser_form(client, tid)
    update_tournament(tid, location="Sala Corrigida, Braga")
    client.post(f"/torneios/{tid}/corrigir", data=form | {"location": "Sala Outra, Braga"})
    [c] = corrections_for(tid)
    assert c["changes"] == {"location": {"old": "Sala Antiga, Braga", "new": "Sala Outra, Braga"}}

    login(client)
    review = client.get(f"/admin/correcoes/{c['id']}").text
    assert "Valor atual: Sala Corrigida, Braga" in review
    assert "Quando foi enviada, o valor era: Sala Antiga, Braga" in review


def test_line_break_in_a_single_line_field_is_not_a_change(client):
    # Imported venues can hold a line break, which browsers drop from inputs.
    tid = make_tournament(location="Pavilhao Municipal\nRua X, Braga")
    r = client.post(f"/torneios/{tid}/corrigir", data=browser_form(client, tid) | {"note": "So uma nota."},
                    follow_redirects=False)
    assert r.status_code == 303
    assert corrections_for(tid)[0]["changes"] == {}


# --- public form details --------------------------------------------------------


def test_correction_pages_are_kept_out_of_search_results(client):
    tid = make_tournament()
    assert '<meta name="robots" content="noindex">' in client.get(f"/torneios/{tid}/corrigir").text
    assert "noindex" not in client.get(f"/torneios/{tid}").text


def test_field_errors_are_summarised_at_the_top(client):
    tid = make_tournament()
    form = browser_form(client, tid) | {"time_control": "60 + 30", "source_url": "www.fpx.pt/reg.pdf"}
    r = client.post(f"/torneios/{tid}/corrigir", data=form)
    assert r.status_code == 400
    assert '<p class="error" role="alert">Há campos por rever mais abaixo: Fonte.</p>' in r.text


def test_changes_are_stored_as_readable_json(client):
    tid = make_tournament(location="Pavilhão Velho, Braga")
    suggest(client, tid, location="Pavilhão Novo, Guimarães")
    with SessionLocal() as db:
        raw = db.execute(text("SELECT changes FROM corrections WHERE tournament_id = :t"), {"t": tid}).scalar_one()
    assert "Pavilhão Novo, Guimarães" in raw


# --- admin side -----------------------------------------------------------------


def test_submitter_email_link_cannot_carry_extra_mail_fields(client):
    tid = make_tournament()
    suggest(client, tid, note="Nota.", submitted_by_email="me@x.pt?bcc=other@evil.example")
    [c] = corrections_for(tid)
    login(client)
    review = client.get(f"/admin/correcoes/{c['id']}").text
    assert 'href="mailto:me%40x.pt%3Fbcc%3Dother%40evil.example"' in review
    assert "mailto:me@x.pt?bcc" not in review


def test_cancelled_flag_comes_with_guidance(client):
    tid = make_tournament()
    client.post(f"/torneios/{tid}/corrigir", data={"cancelled": "1"})
    [c] = corrections_for(tid)
    login(client)
    review = client.get(f"/admin/correcoes/{c['id']}").text
    assert "Não há um campo para cancelamentos" in review
    assert "so a nota" not in review


def test_correction_ids_are_never_reused(client):
    first = make_tournament()
    suggest(client, first, note="Primeira.")
    [old] = corrections_for(first)
    login(client)
    client.post(f"/admin/tournaments/{first}/delete", data={"csrf_token": admin_csrf(client)})

    second = make_tournament()
    suggest(client, second, note="Segunda.")
    [new] = corrections_for(second)
    assert new["id"] > old["id"]
    # a stale reject button for the deleted one cannot touch the new one
    client.post(f"/admin/correcoes/{old['id']}/reject", data={"csrf_token": admin_csrf(client)})
    assert corrections_for(second)[0]["status"] == CorrectionStatus.pending


def test_correction_left_by_a_hand_deleted_tournament_is_not_shown_for_its_reused_id(client):
    tid = make_tournament(location="Sala Velha, Braga")
    suggest(client, tid, location="Sala Nova, Braga")
    [c] = corrections_for(tid)
    with SessionLocal() as db:
        # deleted by hand in SQL without its corrections, as an old recipe did
        db.execute(text("UPDATE corrections SET created_at = '2020-01-01 00:00:00' WHERE id = :c"), {"c": c["id"]})
        db.execute(text("DELETE FROM tournaments WHERE id = :t"), {"t": tid})
        db.commit()
    reused = make_tournament(id=tid, title="Torneio Novo Com Id Reutilizado")
    assert reused == tid

    login(client)
    assert f'href="/admin/correcoes/{c["id"]}"' not in client.get("/admin").text
    assert client.get(f"/admin/correcoes/{c['id']}").status_code == 404


def test_saving_a_form_opened_before_another_save_reloads_instead_of_reverting(client):
    tid = make_tournament(location="Sala Antiga, Braga", time_control="90 + 30")
    login(client)
    tab_one = form_values(client.get(f"/admin/tournaments/{tid}/edit").text)
    tab_two = form_values(client.get(f"/admin/tournaments/{tid}/edit").text)

    r = client.post(f"/admin/tournaments/{tid}/edit", data=tab_one | {"location": "Sala Nova, Braga"},
                    follow_redirects=False)
    assert r.status_code == 303

    r = client.post(f"/admin/tournaments/{tid}/edit", data=tab_two | {"time_control": "60 + 30"})
    assert r.status_code == 409
    assert "Este torneio foi alterado noutra janela" in r.text
    assert 'value="Sala Nova, Braga"' in r.text
    t = get_tournament(tid)
    assert (t.location, t.time_control) == ("Sala Nova, Braga", "90 + 30")

    # the reloaded form saves normally
    reloaded = form_values(r.text) | {"time_control": "60 + 30"}
    assert client.post(f"/admin/tournaments/{tid}/edit", data=reloaded, follow_redirects=False).status_code == 303
    t = get_tournament(tid)
    assert (t.location, t.time_control) == ("Sala Nova, Braga", "60 + 30")


def test_two_review_tabs_cannot_undo_each_other(client):
    tid = make_tournament(location="Sala Antiga, Braga", time_control="90 + 30")
    suggest(client, tid, location="Sala Nova, Braga")
    suggest(client, tid, time_control="60 + 30")
    first, second = corrections_for(tid)
    login(client)
    tab_one = form_values(client.get(f"/admin/correcoes/{first['id']}").text)
    tab_two = form_values(client.get(f"/admin/correcoes/{second['id']}").text)

    assert client.post(f"/admin/tournaments/{tid}/edit", data=tab_one, follow_redirects=False).status_code == 303
    r = client.post(f"/admin/tournaments/{tid}/edit", data=tab_two)
    assert r.status_code == 409
    # reloaded with the first fix kept and the second suggestion still on top
    assert 'value="Sala Nova, Braga"' in r.text
    assert 'value="60 + 30"' in r.text
    assert client.post(f"/admin/tournaments/{tid}/edit", data=form_values(r.text),
                       follow_redirects=False).status_code == 303
    t = get_tournament(tid)
    assert (t.location, t.time_control) == ("Sala Nova, Braga", "60 + 30")
    assert [c["status"] for c in corrections_for(tid)] == [CorrectionStatus.resolved, CorrectionStatus.resolved]


def test_long_description_with_line_breaks_can_be_saved_by_the_admin(client):
    # Fits the 5000 limit when each line break counts once; the browser
    # sends them as CRLF, which used to push it over.
    description = "\n".join(["x" * 101] * 49) + "\n" + "x"
    assert len(description) <= 5000 < len(description.replace("\n", "\r\n"))
    tid = make_tournament(description="curta")
    login(client)
    form = form_values(client.get(f"/admin/tournaments/{tid}/edit").text)
    form["description"] = description.replace("\n", "\r\n")
    assert client.post(f"/admin/tournaments/{tid}/edit", data=form, follow_redirects=False).status_code == 303


def test_edit_form_shows_description_errors(client):
    tid = make_tournament()
    login(client)
    form = form_values(client.get(f"/admin/tournaments/{tid}/edit").text) | {"description": "x" * 5001}
    r = client.post(f"/admin/tournaments/{tid}/edit", data=form)
    assert r.status_code == 400
    assert "Máximo de 5000 caracteres." in r.text
