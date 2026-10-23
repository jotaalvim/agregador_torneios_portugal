import pytest

from app.view import time_control_category, time_control_display, time_control_html, time_control_slug


@pytest.mark.parametrize(
    ("typed", "shown"),
    [
        ("90' + 30''/lance, 9 rondas", "90′ + 30″/lance, 9 rondas"),
        ("3' + 2''", "3′ + 2″"),
        ("15'+5\"", "15′+5″"),
        ("30' + 0''", "30′ + 0″"),
        # Marks not right after a digit are left alone.
        ("Rapidas d'Ouro, 15 min", "Rapidas d'Ouro, 15 min"),
        ("90 min + 30 s", "90 min + 30 s"),
    ],
)
def test_time_control_display_uses_prime_marks(typed, shown):
    assert time_control_display(typed) == shown


def test_time_control_html_wraps_minute_marks():
    assert time_control_html("3' + 2''") == '3<span class="tc-min">′</span> + 2″'


def test_time_control_html_escapes_typed_text():
    html = time_control_html("<b>15' + 5''</b>")
    assert html == '&lt;b&gt;15<span class="tc-min">′</span> + 5″&lt;/b&gt;'


@pytest.mark.parametrize(
    ("typed", "category"),
    [
        # Every ritmo on the site in October 2026, sorted by FIDE's limits:
        # base + increment up to 10 minutes is blitz, under 60 rapid.
        ("3 + 2", "rapidas"),
        ("3+2", "rapidas"),
        ("5 + 3", "rapidas"),
        ("4 + 4", "rapidas"),
        ("5 + 0", "rapidas"),
        ("5 + 5", "rapidas"),
        ("10 + 0", "rapidas"),
        ("10 + 2", "semi-rapidas"),
        ("10 + 5", "semi-rapidas"),
        ("15 + 0", "semi-rapidas"),
        ("15 + 5", "semi-rapidas"),
        ("15 + 10", "semi-rapidas"),
        ("30 + 30", "classicas"),
        ("60 + 30", "classicas"),
        ("90 + 30", "classicas"),
        ("90/40 + 30 + 30", "classicas"),
        # Typed by organizers, as the seed tournaments were.
        ("90' + 30''/lance, 9 rondas", "classicas"),
        ("15' + 5''/lance, 7 rondas", "semi-rapidas"),
        ("3’ + 2’’", "rapidas"),
        ("11 rondas, 3+2", "rapidas"),
        ("10 minutos/jogador + 5 segundos/lance", "semi-rapidas"),
        ("1h + 30s", "classicas"),
        ("1h30 + 30", "classicas"),
        ("Partidas de 45 minutos", "semi-rapidas"),
        # No time in it: shown only with no ritmo picked.
        ("a definir", None),
        ("9 rondas", None),
    ],
)
def test_time_control_category(typed, category):
    assert time_control_category(typed) == category


def test_time_control_slug_ignores_unknown_values():
    assert time_control_slug("Clássicas") == "classicas"
    assert time_control_slug("blitz") == ""
    assert time_control_slug("") == ""
