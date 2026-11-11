from app.matching import Matcher, Rule, normalize


def test_normalize():
    assert normalize("Bio Hafermilch 1L") == "hafermilch"
    assert normalize("H-MILCH 3,5% 1L tetra") == "hmilch tetra"
    assert normalize("BIO RINDERHACK 500G") == "rinderhack"
    assert normalize("FROSCH SPÜLMITTEL 500ML") == "frosch spulmittel"
    assert normalize("GOUDA JUNG 48% FETT") == "gouda jung fett"


def test_matcher_longest_and_compound():
    rules = [
        Rule(id=1, pattern="milch", category_id=1, source="seed"),
        Rule(id=2, pattern="hafermilch", category_id=2, source="seed"),
        Rule(id=3, pattern="reis", category_id=3, source="seed"),
        Rule(id=4, pattern="eis", category_id=4, source="seed"),
    ]
    matcher = Matcher(rules)

    # 1. "hafermilch" must match category 2 (longest/exact match), NOT category 1 (milch)
    m = matcher.match("Bio Hafermilch 1L")
    assert m is not None
    assert m.category_id == 2

    # 2. "Vollmilch" (compound ending with milch) should match "milch" (cat 1)
    m = matcher.match("Bio Frische Vollmilch")
    assert m is not None
    assert m.category_id == 1

    # 3. "Basmati Reis" must match "reis" (cat 3), NOT "eis" (cat 4)!
    m = matcher.match("Bio Basmati Reis 1kg")
    assert m is not None
    assert m.category_id == 3

    # 4. "Eisbergsalat" contains "eis", but should not confuse "reis"
    m = matcher.match("Eis")
    assert m is not None
    assert m.category_id == 4


def test_matcher_source_priority():
    rules = [
        Rule(id=1, pattern="nutella", category_id=1, source="seed"),
        Rule(id=2, pattern="nutella", category_id=2, source="manual"),  # user corrected
    ]
    matcher = Matcher(rules)
    m = matcher.match("Nutella 450g")
    assert m is not None
    assert m.category_id == 2  # manual beats seed


def test_matcher_store_priority():
    rules = [
        Rule(id=1, pattern="kaffee", category_id=1, source="seed", store=None),
        Rule(id=2, pattern="kaffee", category_id=2, source="manual", store="rewe"),
    ]
    matcher = Matcher(rules)
    assert matcher.match("Kaffee", store="Edeka").category_id == 1
    assert matcher.match("Kaffee", store="Rewe").category_id == 2
