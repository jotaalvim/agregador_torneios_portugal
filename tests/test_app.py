import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Status, Tournament

ADMIN = {"username": "testadmin", "password": "testpass"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def login(client: TestClient) -> None:
    r = client.post("/admin/login", data=ADMIN, follow_redirects=False)
    assert r.status_code == 303


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_homepage_lists_seed_tournaments(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "II Open Internacional de Braga" in r.text


def test_robots_and_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /admin" in r.text

    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "<urlset" in r.text
    assert "/torneios/" in r.text


def test_search_filters_results(client):
    r = client.get("/", params={"q": "Guimaraes"})
    assert "Semi-Rapidas de Guimaraes" in r.text
    assert "Open de Outono do Porto" not in r.text


def test_search_no_results_message(client):
    r = client.get("/", params={"q": "torneio-que-nao-existe"})
    assert "Sem torneios próximos" in r.text


SEED_BY_RITMO = {
    "classicas": ["II Open Internacional de Braga", "Open de Outono do Porto"],
    "semi-rapidas": ["IV Semi-Rapidas de Guimaraes"],
    "rapidas": ["Blitz de Ano Novo de Viana do Castelo"],
}


@pytest.mark.parametrize("ritmo", SEED_BY_RITMO)
def test_ritmo_filter_shows_only_that_group(client, ritmo):
    page = client.get("/", params={"ritmo": ritmo}).text
    feed = client.get("/calendario.ics", params={"ritmo": ritmo}).text
    for group, titles in SEED_BY_RITMO.items():
        for title in titles:
            assert (title in page) == (group == ritmo), title
            assert (f"SUMMARY:{title}" in feed) == (group == ritmo), title


def test_ritmo_filter_with_district_and_calendar(client):
    r = client.get("/distrito/braga", params={"ritmo": "semi-rapidas"})
    assert "IV Semi-Rapidas de Guimaraes" in r.text
    assert "II Open Internacional de Braga" not in r.text

    r = client.get("/calendario", params={"distrito": "braga", "ritmo": "classicas"})
    assert "II Open Internacional de Braga" in r.text
    assert "IV Semi-Rapidas de Guimaraes" not in r.text


def test_unknown_ritmo_shows_everything(client):
    r = client.get("/", params={"ritmo": "bogus"})
    assert all(title in r.text for titles in SEED_BY_RITMO.values() for title in titles)


def test_ritmo_is_kept_by_links_and_search(client):
    # How each link is built is tested on view.Filters; this checks the pages use it.
    r = client.get("/distrito/braga", params={"ritmo": "classicas"})
    assert 'href="/distrito/porto?ritmo=classicas"' in r.text
    assert 'href="/calendario?distrito=braga&amp;ritmo=classicas"' in r.text
    assert '<input type="hidden" name="ritmo" value="classicas">' in r.text


def test_ritmo_filter_no_results_message(client):
    r = client.get("/distrito/viana-do-castelo", params={"ritmo": "classicas"})
    assert "Sem torneios de clássicas agendados neste distrito." in r.text


def event_data(page: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, flags=re.S).group(1))


def test_tournament_page_has_event_data_ritmo_and_share_card(client):
    tid = re.search(r'href="/torneios/(\d+)">Open de Outono do Porto</a>', client.get("/").text).group(1)
    page = client.get(f"/torneios/{tid}").text
    event = event_data(page)
    assert (event["@type"], event["name"], event["startDate"]) == ("Event", "Open de Outono do Porto", "2026-11-08")
    assert event["url"].endswith(f"/torneios/{tid}")
    assert 'class="tag tag-ritmo" href="/?ritmo=classicas">Clássicas</a>' in page
    assert re.search(r'<meta property="og:image" content="https?://[^"]+/static/og-image\.png">', page)
    assert client.get("/static/og-image.png").headers["content-type"] == "image/png"
    assert 'href="https://calendar.google.com/calendar/render?action=TEMPLATE' in page


def test_calendar_feed_names_itself_and_links_back(client):
    feed = client.get("/calendario.ics", params={"distrito": "braga", "ritmo": "classicas"}).text
    assert all(len(line.encode("utf-8")) <= 75 for line in feed.split("\r\n"))
    feed = feed.replace("\r\n ", "")  # unfolded, as calendar apps read it
    assert "X-WR-CALNAME:Torneios Xadrez Portugal · Braga · Clássicas" in feed
    event = next(e for e in feed.split("BEGIN:VEVENT") if "SUMMARY:II Open Internacional de Braga" in e)
    assert r"Ritmo: 90' + 30''/lance\, 9 rondas (Clássicas)" in event
    assert re.search(r"URL:https?://\S+/torneios/\d+\r\n", event)

    single = client.get(re.search(r"URL:https?://[^/]+(/torneios/\d+)", event).group(1) + "/ics").text
    assert "X-WR-CALNAME" not in single  # a one-off download, not a subscription


def test_event_data_cannot_close_its_script_tag(client):
    title = "Open </script><script>alert(1)</script>"
    with SessionLocal() as db:
        t = Tournament(title=title, district="Braga", date=date(2027, 6, 1), location="Sala, Braga",
                       time_control="90 + 30", status=Status.approved)
        db.add(t)
        db.commit()
        tid = t.id
    page = client.get(f"/torneios/{tid}").text
    assert "<script>alert(1)" not in page
    assert event_data(page)["name"] == title


def test_district_page_404_for_unknown_slug(client):
    r = client.get("/distrito/does-not-exist")
    assert r.status_code == 404


@pytest.mark.parametrize("url", ["/torneios/999999", "/distrito/does-not-exist", "/pagina-que-nao-existe"])
def test_missing_pages_get_the_site_layout(client, url):
    r = client.get(url)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "Página não encontrada" in r.text
    assert 'href="/calendario"' in r.text  # the masthead, so there is a way on


def test_admin_requires_login(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_admin_login_wrong_password(client):
    r = client.post("/admin/login", data={"username": "testadmin", "password": "wrong"})
    assert r.status_code == 400
    assert "Credenciais inválidas" in r.text


def test_admin_login_success_and_dashboard(client):
    login(client)
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Administração" in r.text


def test_admin_action_rejects_bad_csrf(client):
    login(client)
    r = client.post("/admin/tournaments/1/approve", data={"csrf_token": "bogus"})
    assert r.status_code == 400


def test_submit_validation_errors(client):
    r = client.post("/submeter", data={"title": ""})
    assert r.status_code == 400
    assert "Campo obrigatório" in r.text


def test_submit_honeypot_is_silently_ignored(client):
    r = client.post(
        "/submeter",
        data={
            "title": "Bot Tournament",
            "district": "Porto",
            "date": "2027-06-01",
            "location": "X",
            "time_control": "Y",
            "confirmacao": "filled-by-bot",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/submeter/obrigado"

    login(client)
    assert "Bot Tournament" not in client.get("/admin").text


def test_submit_then_admin_approve_makes_it_public(client):
    r = client.post(
        "/submeter",
        data={
            "title": "Torneio de Teste E2E",
            "district": "Braga",
            "date": "2027-05-01",
            "location": "Sala de Testes",
            "time_control": "15+10",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    # pending, so not public yet
    assert "Torneio de Teste E2E" not in client.get("/").text

    login(client)
    dashboard = client.get("/admin").text
    assert "Torneio de Teste E2E" in dashboard

    match = re.search(
        r'/admin/tournaments/(\d+)/approve" class="inline">\s*'
        r'<input type="hidden" name="csrf_token" value="([^"]+)"',
        dashboard,
    )
    assert match, "approve form for the pending submission was not found"
    tournament_id, csrf_token = match.groups()

    r = client.post(
        f"/admin/tournaments/{tournament_id}/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert r.status_code == 303

    assert "Torneio de Teste E2E" in client.get("/").text


def new_tournament_csrf_token(client: TestClient) -> str:
    form = client.get("/admin/tournaments/new").text
    match = re.search(r'name="csrf_token" value="([^"]+)"', form)
    assert match, "csrf token not found on new tournament form"
    return match.group(1)


def test_admin_new_requires_login(client):
    r = client.get("/admin/tournaments/new", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_admin_new_creates_approved_tournament_directly(client):
    login(client)
    r = client.post(
        "/admin/tournaments/new",
        data={
            "csrf_token": new_tournament_csrf_token(client),
            "title": "Torneio Criado Pelo Admin",
            "district": "Braga",
            "date": "2027-07-01",
            "location": "Sala de Testes",
            "time_control": "15+10",
            "status": "approved",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"

    assert "Torneio Criado Pelo Admin" in client.get("/").text


def test_admin_new_validation_errors(client):
    login(client)
    r = client.post(
        "/admin/tournaments/new",
        data={"csrf_token": new_tournament_csrf_token(client), "title": ""},
    )
    assert r.status_code == 400
    assert "Campo obrigatório" in r.text
