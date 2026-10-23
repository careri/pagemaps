"""Reading a page with a map: a JSON map names a page's lists and fields and says where they are.

A map:

    {"page": "example.attendance",
     "lists": {"attendees": {"item": "<css>",
                             "fields": {"name": {"select": ".x", "text": true, "required": true}, …},
                             "expect": {"minItems": 1}}}}

Field options — deliberately few; anything more belongs in the handler's code:
    select   CSS selector inside the item (default: the item itself); or a list of them, tried in
             order — the first that gives a value wins (the page repair adds its find first)
    type     what a value looks like (TYPES): only the page repair checks it, to tell a candidate
             element from a wrong one; reading itself doesn't
    closest  CSS selector of an ancestor to start from instead of the item
    text     use the element's text (whitespace collapsed)
    attr     use this attribute (for "class": the class names joined by spaces)
    pattern  regex; the value becomes group 1 (no match → no value)
    all      with select + pattern: try every match of select, first that matches the pattern wins
    many     return a list: one value per element matching select
    map      look the value up; for attr "class", the first class name that is a key
    exists   CSS selector; the value is true/false
    required the field must have a value in every item, or extraction fails naming the field
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag


class PageChanged(Exception):
    """The page no longer matches its map — reported by map and field name."""

    def __init__(self, message: str, list_name: str | None = None, field: str | None = None, items: int = 0):
        super().__init__(message)
        self.list_name, self.field, self.items = list_name, field, items


# A value's shape, per field `type`. Loose on purpose: enough to tell a name from a number or a badge.
TYPES = {
    "name": r"(?=.*[^\W\d_]{2}).{2,80}",  # a person: two letters in a row somewhere ("Player 1" too)
    "text": r".{1,500}",
    "number": r"\d{1,4}",
    "id": r"[\w./?=&%-]{1,300}",
    "time": r"\d{1,2}[:.]\d{2}\b.*",
    "mark": r"[A-ZÅÄÖ]{1,4}",
}


def valid(type_: str | None, value: Any) -> bool:
    """Has the shape of `type_` (a list: every element has). "flag" (exists) is any bool."""
    if value in (None, "", []):
        return False
    if type_ == "flag":
        return isinstance(value, bool)
    if isinstance(value, list):
        return all(valid(type_, v) for v in value)
    return type_ is None or re.fullmatch(TYPES[type_], str(value)) is not None


def extract(page: str, html: str, spec: dict) -> dict[str, list[dict[str, Any]]]:
    """Read `html` with the map `spec`: {list name: [item, …]}, each item {field: value}. `page` names
    the page in errors. Raises PageChanged when a required field is missing or a list too short."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, list[dict[str, Any]]] = {}
    for list_name, lspec in spec["lists"].items():
        items = [_item(el, lspec["fields"]) for el in soup.select(lspec["item"])]
        _check(page, list_name, lspec, items)
        out[list_name] = items
    return out


def _item(el: Tag, fields: dict) -> dict[str, Any]:
    return {name: _field(el, f) for name, f in fields.items()}


def _field(item: Tag, f: dict) -> Any:
    root = item.find_parent(_css_predicate(item, f["closest"])) if "closest" in f else item
    if root is None:
        return None
    if "exists" in f:
        return root.select_one(f["exists"]) is not None or _matches(root, f["exists"])
    if isinstance(f.get("select"), list):  # alternatives, in order: the first that gives a value
        one = {k: v for k, v in f.items() if k != "closest"}  # root is already the closest ancestor
        for css in f["select"]:
            v = _field(root, {**one, "select": css})
            if v not in (None, "", []):
                return v
        return [] if f.get("many") else None
    targets = root.select(f["select"]) if "select" in f else [root]
    if f.get("many"):
        return [v for v in (_value(t, f) for t in targets) if v not in (None, "")]
    if f.get("all"):
        for t in targets:
            v = _value(t, f)
            if v not in (None, ""):
                return v
        return None
    return _value(targets[0], f) if targets else None


def _value(el: Tag, f: dict) -> Any:
    if "attr" in f:
        raw = el.get(f["attr"])
        if raw is None:
            return None
        value = " ".join(raw) if isinstance(raw, list) else raw
    else:
        value = text(el)
    if "pattern" in f:
        m = re.search(f["pattern"], value)
        if not m:
            return None
        value = m.group(1).strip()
    if "map" in f:
        if f.get("attr") == "class":
            return next((f["map"][c] for c in value.split() if c in f["map"]), None)
        return f["map"].get(value)
    return value


def text(el: Tag) -> str:
    # get_text skips HTML comments, so markers like Livewire's <!--[if BLOCK]--> vanish here.
    return " ".join(el.get_text(" ").split())


def _matches(el: Tag, css: str) -> bool:
    return el in el.parent.select(css) if el.parent else False


def _css_predicate(item: Tag, css: str):
    candidates = set(map(id, item.find_parents()))
    soup = item
    while soup.parent is not None:
        soup = soup.parent
    matching = {id(t) for t in soup.select(css)} & candidates
    return lambda tag: id(tag) in matching


def _check(page: str, list_name: str, lspec: dict, items: list[dict]) -> None:
    expect = lspec.get("expect", {})
    if len(items) < expect.get("minItems", 0):
        raise PageChanged(f"page {page}: list {list_name} has {len(items)} items, expected at least {expect['minItems']}",
                          list_name, None, len(items))
    for name, f in lspec["fields"].items():
        if not f.get("required"):
            continue
        missing = sum(1 for it in items if it.get(name) in (None, "", []))
        if missing:
            raise PageChanged(f"page {page}: field {list_name}.{name} missing in {missing} of {len(items)} items",
                              list_name, name, len(items))
