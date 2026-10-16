import pytest

from app.view import time_control_display, time_control_html


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
