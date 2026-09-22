import re

import pytest
from fastapi.testclient import TestClient

from app.main import app

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
    assert "Sem torneios proximos" in r.text


def test_district_page_404_for_unknown_slug(client):
    r = client.get("/distrito/does-not-exist")
    assert r.status_code == 404


def test_admin_requires_login(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_admin_login_wrong_password(client):
    r = client.post("/admin/login", data={"username": "testadmin", "password": "wrong"})
    assert r.status_code == 400
    assert "Credenciais invalidas" in r.text


def test_admin_login_success_and_dashboard(client):
    login(client)
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Administracao" in r.text


def test_admin_action_rejects_bad_csrf(client):
    login(client)
    r = client.post("/admin/tournaments/1/approve", data={"csrf_token": "bogus"})
    assert r.status_code == 400


def test_submit_validation_errors(client):
    r = client.post("/submeter", data={"title": ""})
    assert r.status_code == 400
    assert "Campo obrigatorio" in r.text


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
    assert "Campo obrigatorio" in r.text
