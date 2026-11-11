import pytest
from app.engine import CategoryIn, ItemIn, PaymentIn, PersonIn, compute_month, settle


def test_engine_tmm_diet_split():
    # Daniel (vegan, no alcohol/meat, 25 days)
    # Anna (omnivore, 30 days)
    # Ben (omnivore, 20 days)
    persons = [
        PersonIn(id=1, name="Daniel", tags=frozenset()),  # no special tags
        PersonIn(id=2, name="Anna", tags=frozenset(["fleisch", "alkohol", "milch"])),
        PersonIn(id=3, name="Ben", tags=frozenset(["fleisch", "alkohol", "milch"])),
    ]
    days = {1: 25, 2: 30, 3: 20}  # total 75 days

    cats = {
        10: CategoryIn(id=10, required_tag=None, mode="shared"),       # Basis/Vegan
        20: CategoryIn(id=20, required_tag="fleisch", mode="shared"),  # Fleisch
        30: CategoryIn(id=30, required_tag="alkohol", mode="shared"),  # Alkohol
        80: CategoryIn(id=80, required_tag=None, mode="buyer"),        # Privat
    }

    items = [
        # 1. Haferflocken & Gemüse (basis, 75 €) bought by Daniel
        ItemIn(id=1, receipt_id=1, price_cents=7500, category_id=10, buyer_id=1),
        # 2. Rinderhack (fleisch, 50 €) bought by Anna
        # Meat participants: Anna (30d), Ben (20d). Total meat days = 50.
        # Daniel pays 0! Anna pays 30/50*50=30€, Ben pays 20/50*50=20€.
        ItemIn(id=2, receipt_id=2, price_cents=5000, category_id=20, buyer_id=2),
        # 3. Kiste Bier (alkohol, 25 €) bought by Ben
        # Alcohol participants: Anna (30d), Ben (20d).
        # Anna pays 15€, Ben pays 10€. Daniel pays 0!
        ItemIn(id=3, receipt_id=3, price_cents=2500, category_id=30, buyer_id=3),
        # 4. Zahnbürste (privat, 3 €) bought by Daniel
        ItemIn(id=4, receipt_id=1, price_cents=300, category_id=80, buyer_id=1),
    ]

    res = compute_month(persons, days, cats, items)

    # Total shared costs: 75 + 50 + 25 = 150 €
    assert res.total_cents == 15000
    assert res.private_cents == 300
    assert res.total_days == 75

    # Check Daniel (id 1)
    # Basis: 25/75 of 75€ = 25.00 €
    # Meat: 0 €
    # Alcohol: 0 €
    # Soll = 25.00 €
    # Haben = 75.00 € (the basis purchase; private 3€ is not shared)
    # Saldo = 75 - 25 = +50.00 € (Daniel gets 50€ back!)
    p1 = res.persons[1]
    assert p1.soll == 2500
    assert p1.haben == 7500
    assert p1.private == 300
    assert p1.saldo == 5000

    # Check Anna (id 2)
    # Basis: 30/75 of 75€ = 30.00 €
    # Meat: 30/50 of 50€ = 30.00 €
    # Alcohol: 30/50 of 25€ = 15.00 €
    # Soll = 30 + 30 + 15 = 75.00 €
    # Haben = 50.00 €
    # Saldo = 50 - 75 = -25.00 € (Anna owes 25€)
    p2 = res.persons[2]
    assert p2.soll == 7500
    assert p2.haben == 5000
    assert p2.saldo == -2500

    # Check Ben (id 3)
    # Basis: 20/75 of 75€ = 20.00 €
    # Meat: 20/50 of 50€ = 20.00 €
    # Alcohol: 20/50 of 25€ = 10.00 €
    # Soll = 20 + 20 + 10 = 50.00 €
    # Haben = 25.00 €
    # Saldo = 25 - 50 = -25.00 € (Ben owes 25€)
    p3 = res.persons[3]
    assert p3.soll == 5000
    assert p3.haben == 2500
    assert p3.saldo == -2500

    # Settlement: Anna pays Daniel 25€, Ben pays Daniel 25€
    assert len(res.transfers) == 2
    transfers_map = {(t.from_id, t.to_id): t.cents for t in res.transfers}
    assert transfers_map[(2, 1)] == 2500
    assert transfers_map[(3, 1)] == 2500


def test_override_persons():
    persons = [PersonIn(1, "A"), PersonIn(2, "B"), PersonIn(3, "C")]
    days = {1: 10, 2: 10, 3: 10}
    cats = {10: CategoryIn(10, None, "shared")}
    # Item specifically for A and B only
    items = [ItemIn(1, 1000, 10, buyer_id=1, override_persons=(1, 2))]
    res = compute_month(persons, days, cats, items)
    assert res.persons[1].soll == 500
    assert res.persons[2].soll == 500
    assert res.persons[3].soll == 0


def test_zero_days_person_pays_nothing():
    persons = [PersonIn(1, "A"), PersonIn(2, "B")]
    days = {1: 20, 2: 0}  # B was away whole month
    cats = {10: CategoryIn(10, None, "shared")}
    items = [ItemIn(1, 1000, 10, buyer_id=2)]  # B bought it anyway
    res = compute_month(persons, days, cats, items)
    assert res.persons[2].soll == 0
    assert res.persons[1].soll == 1000
    assert res.persons[2].saldo == 1000
    assert res.persons[1].saldo == -1000


def test_settle_algorithm():
    # Balances: A: +100, B: -40, C: -60
    balances = {1: 100, 2: -40, 3: -60}
    transfers = settle(balances)
    assert len(transfers) == 2
    assert sum(t.cents for t in transfers) == 100
    assert all(t.to_id == 1 for t in transfers)
