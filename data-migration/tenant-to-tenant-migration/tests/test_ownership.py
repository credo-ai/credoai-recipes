import logging

from kit.ownership import keep_custom

ITEMS = [
    {"id": "a", "own": "custom", "archived": False},
    {"id": "b", "own": "custom", "archived": True},
    {"id": "c", "own": "ootb", "archived": False},
    {"id": "d", "own": "transferred", "archived": True},
    {"id": "e", "own": "transferred", "archived": False},
]


def test_keeps_only_live_custom_items(caplog):
    with caplog.at_level(logging.INFO):
        kept = keep_custom("things", ITEMS, lambda i: i["own"], lambda i: i["archived"])
    assert [i["id"] for i in kept] == ["a"]
    assert "transferred" in caplog.text and "archived" in caplog.text


def test_warns_about_live_transferred_items(caplog):
    with caplog.at_level(logging.WARNING):
        keep_custom("things", ITEMS, lambda i: i["own"], lambda i: i["archived"])
    assert "'e'" in caplog.text


def test_unexpected_ownership_types_are_skipped_with_a_warning(caplog):
    items = [{"id": "x", "own": None, "archived": False}, {"id": "y", "own": "something_new", "archived": False}]
    with caplog.at_level(logging.WARNING):
        kept = keep_custom("things", items, lambda i: i["own"], lambda i: i["archived"])
    assert kept == []
    assert "unexpected ownership type" in caplog.text and "'x'" in caplog.text and "'y'" in caplog.text


def test_long_id_lists_say_first_20(caplog):
    items = [{"id": f"t{n}", "own": "transferred", "archived": False} for n in range(25)]
    with caplog.at_level(logging.WARNING):
        keep_custom("things", items, lambda i: i["own"], lambda i: i["archived"])
    assert "first 20:" in caplog.text and "'t19'" in caplog.text and "'t20'" not in caplog.text
