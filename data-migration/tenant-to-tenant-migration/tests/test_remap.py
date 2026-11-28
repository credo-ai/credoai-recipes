import pytest

from kit.remap import AmbiguousMatch, NameIndex, casefold


def test_exact_lookup_ignores_surrounding_space_only():
    index = NameIndex([{"id": "t1", "name": "Model Risk"}])
    assert index.lookup(" Model Risk ") == "t1"
    assert index.lookup("model risk") is None
    assert index.lookup("Other") is None


def test_duplicate_names_are_never_guessed():
    index = NameIndex([{"id": "t1", "name": "Dup"}, {"id": "t2", "name": "Dup"}])
    with pytest.raises(AmbiguousMatch, match="2 target items named 'Dup'"):
        index.lookup("Dup")


def test_casefold_for_emails():
    index = NameIndex([{"id": "u1", "email": "Pat@Example.com"}], name_of=lambda u: u["email"], normalize=casefold)
    assert index.lookup("pat@example.COM") == "u1"


def test_add_makes_new_items_findable():
    index = NameIndex([])
    index.add("New", "t9")
    assert index.lookup("New") == "t9"
