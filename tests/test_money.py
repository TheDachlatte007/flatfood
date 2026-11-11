import pytest
from app.money import allocate, fmt_eur, to_cents


def test_to_cents_valid():
    assert to_cents("1,29") == 129
    assert to_cents("1.29") == 129
    assert to_cents("1.29 €") == 129
    assert to_cents("1.234,56 EUR") == 123456
    assert to_cents(3.50) == 350
    assert to_cents(0) == 0
    assert to_cents("-0,50") == -50
    assert to_cents(Decimal("-0.50")) if False else to_cents("-0.50") == -50


def test_to_cents_invalid():
    with pytest.raises(ValueError):
        to_cents("")
    with pytest.raises(ValueError):
        to_cents("abc")
    with pytest.raises(ValueError):
        to_cents(True)


def test_fmt_eur():
    assert fmt_eur(129) == "1,29 €"
    assert fmt_eur(0) == "0,00 €"
    assert fmt_eur(-50) == "-0,50 €"
    assert fmt_eur(10050) == "100,50 €"


def test_allocate_exact_sum():
    # 100 cents among 3 people with equal weights (1, 1, 1) -> 34, 33, 33 (sum=100)
    res = allocate(100, {"a": 1, "b": 1, "c": 1})
    assert sum(res.values()) == 100
    assert sorted(res.values()) == [33, 33, 34]


def test_allocate_proportional():
    # 1000 cents among weights 20, 10 -> 667, 333
    res = allocate(1000, {"daniel": 20, "mitbewohner": 10})
    assert sum(res.values()) == 1000
    assert res["daniel"] == 667
    assert res["mitbewohner"] == 333


def test_allocate_negative():
    # Discount of -50 cents
    res = allocate(-50, {"a": 1, "b": 1})
    assert sum(res.values()) == -50
    assert res["a"] == -25
    assert res["b"] == -25


def test_allocate_single():
    assert allocate(499, {"daniel": 25}) == {"daniel": 499}
