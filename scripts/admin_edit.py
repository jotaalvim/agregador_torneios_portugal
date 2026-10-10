"""Edit tournaments on the live site through the admin UI.

Reads admin credentials from .env (ADMIN_USERNAME / ADMIN_PASSWORD), logs in,
and PATCHes individual fields of a tournament, leaving every other field as it
already is. The edit form is all-or-nothing — it rewrites every column from the
POST body — so this fetches the current values first and only overrides the
fields you name.

    # see what would change, without sending anything
    python scripts/admin_edit.py 14 --set date=2026-10-31 --dry-run

    # apply it
    python scripts/admin_edit.py 14 --set date=2026-10-31 \
        --set 'location=Pavilhão Municipal de Lagos, Lagos'

    # find the id of a tournament by title
    python scripts/admin_edit.py --find "Open de Benfica"

Standard library only, so it runs without the app's virtualenv.
"""

import argparse
import html
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (compatible; torneiosdexadrez.pt admin)"

# Every field the edit form posts. Anything omitted from the POST is stored as
# empty, so all of them get round-tripped even when only one is being changed.
FIELDS = [
    "title", "district", "date", "location", "time_control", "organizer",
    "chess_results_url", "regulation_url", "registration_url", "link",
    "description", "status", "admin_note",
]


def load_env(path: str) -> dict:
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def make_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def get(opener, url: str) -> str:
    with opener.open(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def post(opener, url: str, data: dict) -> tuple[str, str]:
    body = urllib.parse.urlencode(data).encode()
    with opener.open(urllib.request.Request(url, data=body), timeout=60) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def login(opener, site: str, username: str, password: str) -> None:
    get(opener, f"{site}/admin/login")
    _, final = post(opener, f"{site}/admin/login", {"username": username, "password": password})
    if final.rstrip("/").endswith("/admin/login"):
        raise SystemExit("Login falhou: credenciais invalidas ou rate-limit (5 tentativas / 15 min).")


def field_value(page: str, name: str) -> str:
    """Current value of one form field, from the rendered edit page."""
    if name == "description" or name == "admin_note":
        m = re.search(rf'<textarea[^>]*name="{name}"[^>]*>(.*?)</textarea>', page, flags=re.S)
        return html.unescape(m.group(1)).strip() if m else ""
    if name == "status":
        m = re.search(r'<option value="([^"]*)"[^>]*\bselected\b', page)
        return html.unescape(m.group(1)) if m else ""
    m = re.search(rf'<input[^>]*name="{name}"[^>]*>', page)
    if not m:
        return ""
    v = re.search(r'value="([^"]*)"', m.group(0))
    return html.unescape(v.group(1)) if v else ""


def read_form(opener, site: str, tid: int) -> dict:
    page = get(opener, f"{site}/admin/tournaments/{tid}/edit")
    values = {f: field_value(page, f) for f in FIELDS}
    values["csrf_token"] = field_value(page, "csrf_token")
    if not values["csrf_token"]:
        raise SystemExit(f"Nao encontrei o csrf_token no formulario do torneio {tid}.")
    return values


def find(opener, site: str, needle: str) -> None:
    page = get(opener, f"{site}/admin")
    needle_low = needle.lower()
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S)
    hits = 0
    for row in rows:
        ids = re.findall(r"/admin/tournaments/(\d+)/edit", row)
        if not ids:
            continue
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", row)).split())
        if needle_low in text.lower():
            print(f"  {ids[0]}: {text[:140]}")
            hits += 1
    if not hits:
        print(f"Nada encontrado para {needle!r}.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tournament_id", nargs="?", type=int)
    p.add_argument("--set", action="append", default=[], metavar="CAMPO=VALOR",
                   help=f"campo a alterar; um de: {', '.join(FIELDS)}")
    p.add_argument("--find", metavar="TEXTO", help="procurar o id de um torneio pelo titulo")
    p.add_argument("--show", action="store_true", help="mostrar os valores atuais e sair")
    p.add_argument("--site", default="https://torneiosdexadrez.pt")
    p.add_argument("--env", default=".env")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    env = load_env(args.env)
    user, pw = env.get("ADMIN_USERNAME"), env.get("ADMIN_PASSWORD")
    if not user or not pw:
        raise SystemExit(f"ADMIN_USERNAME/ADMIN_PASSWORD em falta em {args.env}.")

    site = args.site.rstrip("/")
    opener = make_opener()
    login(opener, site, user, pw)

    if args.find:
        find(opener, site, args.find)
        return
    if args.tournament_id is None:
        raise SystemExit("Indica o id do torneio, ou usa --find TEXTO.")

    values = read_form(opener, site, args.tournament_id)
    if args.show:
        for f in FIELDS:
            print(f"  {f}: {values[f]!r}")
        return

    changes = {}
    for item in args.set:
        if "=" not in item:
            raise SystemExit(f"--set espera CAMPO=VALOR, recebi {item!r}.")
        k, v = item.split("=", 1)
        if k not in FIELDS:
            raise SystemExit(f"Campo desconhecido {k!r}. Validos: {', '.join(FIELDS)}")
        changes[k] = v

    for k, v in changes.items():
        if values[k] == v:
            print(f"  (sem alteracao) {k}: ja e {v!r}")
        else:
            print(f"  {k}: {values[k]!r} -> {v!r}")
    if not any(values[k] != v for k, v in changes.items()):
        print("Nada a alterar.")
        return
    if args.dry_run:
        print("DRY-RUN: nada foi enviado.")
        return

    values.update(changes)
    try:
        body, final = post(opener, f"{site}/admin/tournaments/{args.tournament_id}/edit", values)
    except urllib.error.HTTPError as e:
        errors = re.findall(r'<p class="error">(.*?)</p>', e.read().decode("utf-8", "replace"))
        raise SystemExit(f"Falhou ({e.code}): {errors}")
    if final.rstrip("/").endswith("/admin"):
        print(f"OK: torneio {args.tournament_id} atualizado.")
    else:
        errors = re.findall(r'<p class="error">(.*?)</p>', body)
        raise SystemExit(f"Resposta inesperada ({final}): {errors}")


if __name__ == "__main__":
    main()
