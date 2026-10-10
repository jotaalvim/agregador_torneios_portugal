"""Regression tests for the tournament import scripts in `scripts/`.

Every string here was observed on a real source page. These parsers are the
place import bugs actually come from — a wrong time control or district goes
straight onto the public site — so the cases are kept verbatim rather than
simplified.

Pure parsing only: nothing here touches the network.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_sources import (  # noqa: E402
    guess_district,
    parse_fpx_dates,
    parse_ical,
    tidy_location,
)
from scrape_chess_results import normalize_title, parse_time_control  # noqa: E402


# --- time control -----------------------------------------------------------
#
# In Portuguese chess, "Rápidas" is BLITZ (3+2) and "Semi-rápidas" is rapid
# (10+5). Reading them as the English cognates put a Braga tournament on the
# site as 90+30 when it is 15+3. See SCRAPING.md.

@pytest.mark.parametrize("raw,expected", [
    # chess-results writes the same thing a dozen ways
    ("90 minutes + 30 seconds increment", "90 + 30"),
    ("90 minutos + 30 segundos por lance", "90 + 30"),
    ("90 min + 30 s", "90 + 30"),
    ("1h30m + 30s", "90 + 30"),
    ("90´Finish com 30´´ Incremento desde o lance 1", "90 + 30"),
    ("60´ + 30´´", "60 + 30"),
    ("30´ + 30´´ INCREMENTO JOGADOR/PARTIDA", "30 + 30"),
    ("30´+ 30´´ INCREMENTO JOGADOR/PARTIDA", "30 + 30"),
    ("15 minutos + 5 seg/lance", "15 + 5"),
    ("15'+10''", "15 + 10"),
    ("10 min + 5 seg./lance", "10 + 5"),
    ("10'+5´´/ lance", "10 + 5"),
    ("10min+5s", "10 + 5"),
    ("10+5'", "10 + 5"),
    ("10+2", "10 + 2"),
    ("10minutos + 0 segundos de incremento", "10 + 0"),
    ("5 min + 5 seg./lance", "5 + 5"),
    ("4´+ 4´´ / lance", "4 + 4"),
    ("3 minutes for game with 2 seconds increment from move 1", "3 + 2"),
    ("3´ + 2´´", "3 + 2"),
    ("3+2", "3 + 2"),
])
def test_parse_time_control_normalizes(raw, expected):
    assert parse_time_control(raw) == expected


@pytest.mark.parametrize("raw", [
    # Multi-period and knockout controls have no "X + Y" form. Passing them
    # through unchanged is deliberate: a human tidies them, rather than the
    # parser inventing a plausible-looking wrong answer.
    "90´/40 MOVES + 30´FINISH WITH 30´´ INC. SINCE MOVE 1",
    "15' KO",
    "5´ KO",
    '15"',
])
def test_parse_time_control_leaves_odd_formats_alone(raw):
    assert parse_time_control(raw) == raw


# --- district ---------------------------------------------------------------

@pytest.mark.parametrize("venue,expected", [
    # chess-results filed this one under Santarém; the venue is in Porto.
    ("CINEMAX, R. Dom António Ferreira Gomes nº104, 4560-260 Penafiel", "Porto"),
    ("R. das Escolas do Cabido 179, 4585-584 Recarei", "Porto"),
    ("Torre da Oliva, São João da Madeira", "Aveiro"),
    ("Pavilhão Municipal, Montemor-o-Velho", "Coimbra"),
    ("Sweet Atlantic Hotel, Figueira da Foz", "Coimbra"),
    ("ACDR Arneirense, R. Casal dos Barreiros 2, Caldas da Rainha", "Leiria"),
    ("Sport Operário Marinhense, Rua 25 de Abril 30, Marinha Grande", "Leiria"),
    ("Portimão Arena, 8500-726 Portimão", "Faro"),
    ("Pavilhão Municipal de Lagos, Lagos", "Faro"),
    ("Funchal, Madeira", "Madeira"),
    ("Palace Hotel & SPA Monte Rio, Estr. da Lameira, São Pedro do Sul", "Viseu"),
    ("Polidesportivo Vale Fundão, Marvila, Lisboa", "Lisboa"),
    ("GDC Casquilhos, Barreiro", "Setúbal"),
    ("Rua Camilo Castelo Branco 23, Arrentela, Seixal", "Setúbal"),
])
def test_guess_district(venue, expected):
    assert guess_district(venue) == expected


@pytest.mark.parametrize("venue,expected", [
    # Ties on position are broken by length, so these near-collisions resolve.
    ("Sede do Glória FC, Vila Real de Santo António", "Faro"),
    ("Pavilhão de Vila Real", "Vila Real"),
    ("Torre da Oliva, São João da Madeira", "Aveiro"),
    ("A.XAT Montemor-o-Novo", "Évora"),
    ("CX Montemor-o-Velho", "Coimbra"),
])
def test_guess_district_prefers_longest_match(venue, expected):
    assert guess_district(venue) == expected


@pytest.mark.parametrize("venue,expected", [
    # A street named after somewhere else must not beat the actual town,
    # which comes later in the address. This is a real Seixaliada venue.
    ("Rua Camilo Castelo Branco 23, Arrentela, Seixal", "Setúbal"),
    ("Av. Coimbra 14, Barreiro", "Setúbal"),
    ("Rua de Braga, Porto", "Porto"),
])
def test_guess_district_prefers_the_town_over_a_street_name(venue, expected):
    assert guess_district(venue) == expected


def test_guess_district_gives_up_rather_than_guessing():
    assert guess_district("Local a anunciar") is None
    assert guess_district("") is None
    assert guess_district("Sede do clube") is None


# --- FPX dates --------------------------------------------------------------
#
# The FPX pages drop the "de" particles inconsistently. Requiring them
# silently dropped the Mestres/Honra and Jovens Semi-Rápidas events.

@pytest.mark.parametrize("text,expected", [
    ("17 de outubro de 2026", ("2026-10-17", None)),
    ("09 de maio 2027", ("2027-05-09", None)),
    ("12 a 18 de julho de 2027", ("2027-07-12", "2027-07-18")),
    ("18 a 23 dezembro de 2026", ("2026-12-18", "2026-12-23")),
    ("20 a 24 de março de 2027", ("2027-03-20", "2027-03-24")),
    ("10 e 11 de julho de 2027", ("2027-07-10", "2027-07-11")),
    ("- 09/01/2027 a 18/07/2027", ("2027-01-09", "2027-07-18")),
    ("sem data nenhuma aqui", (None, None)),
])
def test_parse_fpx_dates(text, expected):
    assert parse_fpx_dates(text) == expected


def test_parse_fpx_dates_spans_two_months_with_one_year():
    # Only the end date carries a year; the start must inherit it.
    assert parse_fpx_dates("29 agosto a 01 de setembro de 2027") == ("2027-08-29", "2027-09-01")


def test_parse_fpx_dates_range_crossing_new_year():
    # A first month later than the last means the range crosses New Year,
    # so the start belongs to the previous year.
    assert parse_fpx_dates("28 dezembro a 03 de janeiro de 2027") == ("2026-12-28", "2027-01-03")


# --- venue text -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Soure, Soure, Portugal", "Soure"),
    ("Coimbra, Coimbra", "Coimbra"),
    ("Torre da Oliva, R. X 591,, São João da Madeira, 3700-204, Portugal",
     "Torre da Oliva, R. X 591, São João da Madeira, 3700-204"),
    ("Portimão", "Portimão"),
    ("", ""),
])
def test_tidy_location(raw, expected):
    assert tidy_location(raw) == expected


# --- iCal -------------------------------------------------------------------

def _ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\n" + "".join(events) + "END:VCALENDAR\r\n"


ALL_DAY = (
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20261031\r\n"
    "DTEND;VALUE=DATE:20261108\r\n"
    "SUMMARY:IV Open Internacional Cidade de Lagos\r\n"
    "LOCATION:Pavilhão Municipal de Lagos\\, Lagos\\, Portugal\r\n"
    "URL:https://example.org/lagos/\r\n"
    "END:VEVENT\r\n"
)

TIMED = (
    "BEGIN:VEVENT\r\n"
    "DTSTART:20261031T150000\r\n"
    "DTEND:20261031T190000\r\n"
    "SUMMARY:Taça da AXL - R1\r\n"
    "LOCATION:Polidesportivo Vale Fundão\\, Marvila\\, Lisboa\r\n"
    "END:VEVENT\r\n"
)


def test_parse_ical_all_day_end_is_exclusive():
    # iCal all-day DTEND is the day *after* the last day: 20261108 means the
    # event ends on the 7th. Getting this wrong adds a phantom extra day.
    (t,) = parse_ical(_ics(ALL_DAY), "pct", "")
    assert (t["date"], t["end_date"]) == ("2026-10-31", "2026-11-07")
    assert t["location"] == "Pavilhão Municipal de Lagos, Lagos"
    assert t["district"] == "Faro"
    assert t["link"] == "https://example.org/lagos/"


def test_parse_ical_timed_single_day_has_no_end_date():
    (t,) = parse_ical(_ics(TIMED), "axl", "Associação de Xadrez de Lisboa")
    assert (t["date"], t["end_date"]) == ("2026-10-31", None)
    assert t["organizer"] == "Associação de Xadrez de Lisboa"
    assert t["district"] == "Lisboa"


def test_parse_ical_unfolds_wrapped_lines():
    # RFC 5545 folds a long line by inserting CRLF + one space, and unfolding
    # removes both — it must not leave a space behind, or every wrapped title
    # gains one. Here the content's own space sits before the fold.
    folded = (
        "BEGIN:VEVENT\r\n"
        "DTSTART;VALUE=DATE:20270425\r\n"
        "SUMMARY:Internacional Cidade Braga - Dia da \r\n"
        " Liberdade 2027\r\n"
        "LOCATION:Ludoteca da Estufa\\, Parque da Ponte\\, Braga\r\n"
        "END:VEVENT\r\n"
    )
    (t,) = parse_ical(_ics(folded), "pct", "")
    assert t["title"] == "Internacional Cidade Braga - Dia da Liberdade 2027"
    assert t["district"] == "Braga"


def test_parse_ical_leaves_time_control_empty():
    # Feeds never carry a time control. It must stay empty so the review step
    # and gaps.py both flag it, rather than being guessed.
    for t in parse_ical(_ics(ALL_DAY, TIMED), "pct", ""):
        assert t["time_control"] == ""


# --- ledger dedupe ----------------------------------------------------------

def test_normalize_title_ignores_case_accents_and_punctuation():
    assert normalize_title("5º Torneio do Centenário") == normalize_title("5.º TORNEIO DO CENTENARIO")
    assert normalize_title('IV open Internacional "Cidade de Lagos"') == \
        normalize_title("IV Open Internacional Cidade de Lagos")


def test_normalize_title_still_separates_different_tournaments():
    assert normalize_title("CD Equipas Rápidas") != normalize_title("CD Equipas Semi-Rápidas")
