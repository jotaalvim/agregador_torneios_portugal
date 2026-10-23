import pytest

from app.view import Filters, ritmo_of, time_control_display, time_control_html


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
    ("time_control", "ritmo"),
    [
        # Every time control on the site in October 2026, by FIDE's limits:
        # base + increment up to 10 minutes is blitz, under 60 rapid.
        ("3 + 2", "rapidas"),
        ("3+2", "rapidas"),
        ("4 + 4", "rapidas"),
        ("5 + 0", "rapidas"),
        ("5 + 3", "rapidas"),
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
        # As organizers type them.
        ("90' + 30''/lance, 9 rondas", "classicas"),
        ("15’ + 5’’", "semi-rapidas"),
        ("90 min + 30 s", "classicas"),
        ("11 rondas, 3+2", "rapidas"),
        # No base + increment to read: shown only with no ritmo picked.
        ("a definir", None),
        ("2h + 30s", None),
    ],
)
def test_ritmo_of(time_control, ritmo):
    assert ritmo_of(time_control) == ritmo


def test_filters_url_changes_one_filter_and_keeps_the_rest():
    f = Filters(district="braga", ritmo="classicas", q="open")
    assert f.url(ritmo="rapidas") == "/distrito/braga?q=open&ritmo=rapidas"
    assert f.url(district="porto", q="") == "/distrito/porto?ritmo=classicas"
    assert f.url(view="calendar") == "/calendario?distrito=braga&ritmo=classicas"
    assert f.feed_url == "/calendario.ics?distrito=braga&ritmo=classicas"
    assert Filters().url() == "/"
