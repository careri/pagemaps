"""pagemaps on a made-up roster page, changed the ways sites change their markup."""

import json

import pytest

from pagemaps import PageChanged, changes, counts, extract, fingerprints, repair, same_layout, valid

SPEC = {
    "page": "example.team",
    "lists": {
        "players": {
            "item": "ul.roster > li",
            "fields": {
                "number": {"type": "number", "select": "span.num", "text": True, "pattern": r"(\d+)"},
                "name": {"type": "name", "select": "span.name", "text": True, "required": True},
                "badge": {"type": "mark", "select": "span.badge", "text": True, "many": True},
            },
            "expect": {"minItems": 1},
        }
    },
}


def page(rows=12, name_class="name", row_class=None):
    """A team page: a header, then one row per player — some rows have a badge (C, GK), like real ones."""
    li = f' class="{row_class}"' if row_class else ""
    out = ['<html><body><header><span class="title">Example FC</span></header><ul class="roster">']
    for i in range(1, rows + 1):
        badge = ' <span class="badge">C</span>' if i == 3 else ' <span class="badge">GK</span>' if i == 1 else ""
        out.append(f'<li{li}><span class="num">{i + 10}</span><span class="wrap"><span class="{name_class} bold">'
                   f'Player {i}</span>{badge}</span><span class="stat">0</span></li>')
    out.append("</ul></body></html>")
    return "".join(out)


def read(html, spec=SPEC):
    return extract("example.team", html, spec)


def test_reads_a_page():
    players = read(page())["players"]
    assert len(players) == 12
    assert players[0] == {"number": "11", "name": "Player 1", "badge": ["GK"]}


def test_select_alternatives_first_with_a_value_wins():
    spec = json.loads(json.dumps(SPEC))
    spec["lists"]["players"]["fields"]["name"]["select"] = ["span.full-name", "span.name"]
    assert read(page(), spec)["players"][1]["name"] == "Player 2"


def test_a_missing_required_field_names_list_and_field():
    with pytest.raises(PageChanged, match=r"field players.name missing in 12 of 12 items") as e:
        read(page(name_class="title-text"))
    assert (e.value.list_name, e.value.field, e.value.items) == ("players", "name", 12)


def test_a_renamed_class_is_found_again():
    old, new = page(), page(name_class="player-name")
    proposal = repair("example.team", new, SPEC, fingerprints(old, SPEC))
    assert proposal is not None
    assert read(new, proposal) == read(old)
    [change] = changes(SPEC, proposal)
    # either class on the name reads the same values: no guess either way
    assert change["after"][0] in ("span.player-name", "span.bold") and change["after"][1:] == ["span.name"]
    assert counts("example.team", new, proposal)["players"] == {"items": 12, "fields": {"number": 12, "name": 12, "badge": 2}}


def test_a_list_that_lost_its_items_gets_a_new_item_selector():
    spec = json.loads(json.dumps(SPEC))
    spec["lists"]["players"]["item"] = "ul.roster > li.row"
    old, new = page(row_class="row"), page(row_class="player-row")
    proposal = repair("example.team", new, spec, fingerprints(old, spec))
    assert proposal is not None and read(new, proposal) == read(old, spec)
    assert changes(spec, proposal)[0]["field"] is None  # the item selector


def test_a_field_gone_from_the_page_is_not_guessed():
    old = page()
    gone = old.replace('<span class="name bold">', '<span>').replace("Player ", "")
    assert repair("example.team", gone, SPEC, fingerprints(old, SPEC)) is None


def test_no_fingerprints_no_repair():
    assert repair("example.team", page(name_class="x"), SPEC, None) is None


def test_fingerprints_hold_no_text():
    stored = json.dumps(fingerprints(page(), SPEC))
    assert "Player" not in stored and "Example FC" not in stored


def test_a_row_more_or_less_is_the_same_layout():
    assert same_layout(fingerprints(page(rows=12), SPEC), fingerprints(page(rows=9), SPEC))
    assert not same_layout(fingerprints(page(), SPEC), fingerprints(page(name_class="other"), SPEC))


@pytest.mark.parametrize("type_, value, ok", [
    ("name", "Player 1", True), ("name", "12", False), ("number", "7", True), ("number", "–", False),
    ("mark", ["C", "GK"], True), ("mark", ["captain"], False), ("flag", False, True), (None, "x", True),
])
def test_types(type_, value, ok):
    assert valid(type_, value) is ok


def test_validate_a_map():
    from pagemaps import validate

    assert validate(SPEC) == [] and validate(SPEC, like=SPEC) == []
    bad = json.loads(json.dumps(SPEC))
    f = bad["lists"]["players"]["fields"]
    f["name"]["select"] = ["span.name", "span[unclosed"]
    f["number"]["pattern"] = "\\d+"
    f["badge"]["fetch"] = "https://elsewhere.example"
    del f["number"]["type"]
    bad["lists"]["players"]["fields"]["extra"] = {"type": "colour"}
    problems = validate(bad, like=SPEC)
    assert any("select[1]" in p for p in problems)
    assert any("pattern: needs a group" in p for p in problems)
    assert any("unknown option 'fetch'" in p for p in problems)
    assert any("extra.type" in p for p in problems)
    del bad["lists"]["players"]["fields"]["name"]
    assert any("fields.name: missing" in p for p in validate(bad, like=SPEC))
    assert validate("not a map") == ['a map is an object with "lists"']
