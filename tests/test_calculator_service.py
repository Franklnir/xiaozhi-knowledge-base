import pytest

from xiaozhi.services.calculator_service import safe_eval


@pytest.mark.parametrize(("expression", "expected"), [
    ("2 + 3 * 4", 14),
    ("berapa 15% dari 250000?", 37500),
    ("akar dari 144", 12),
    ("konversi 100 fahrenheit ke celsius", 37.77777777777778),
    ("math.sqrt(81) + ceil(1.2)", 11),
    ("LOG10(1000) + ln(e)", 4),
    ("2x3 + 2^4", 22),
    ("sum([1, 2, 3])", 6),
    ("1e3 / 2", 500),
    ("10 / 50%", 20),
])
def test_calculator_numeric_input(expression, expected):
    result = safe_eval(expression)
    assert result["success"] is True
    assert result["result"] == pytest.approx(expected)


@pytest.mark.parametrize("expression", [
    "1/0", "unknown(3)", "math.pi.real", "1000 ** 1000",
    "1e101", "(-1)**0.5", "round(1.2, 1000)", "1 # trailing comment",
])
def test_calculator_rejects_invalid_or_unbounded_input(expression):
    assert safe_eval(expression)["success"] is False
