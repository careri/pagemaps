"""Page repair: when a map no longer reads a page, find its fields again and propose a new map.

The caller stores what `fingerprints` returns (after a good read) and passes it back to `repair`
(after a failed one); nothing is stored here.

- After a good read, `fingerprints` describes, per list and field, the element that held it: tag,
  class names, the path of tag names, the parent and its classes, the siblings' and children's tags,
  and how many items there were. **No text and no attribute values but classes** — pages often hold
  personal data. The format is Scrapling's (`element_to_dict`), so its scoring can use it.
- When a read fails on a field, `repair` lets Scrapling score every element of the new page against
  that fingerprint. Scrapling returns only the best-scoring elements — a few rows, not all of them
  (rows with a badge differ) — so the candidates are selectors made from what they share (a tag, a
  class, a data attribute), and each is tried on every item with the field's own options.
- A candidate is accepted when the field then has a value of its `type` in ≥ 95 % of the items and no
  other candidate does as well with different values. The proposed map has it first in that field's
  `select` list (the old selector stays as a fallback) and must read the whole page, with items in
  every list it repaired. Never applied here: the caller decides.
"""

from __future__ import annotations

import copy
from typing import Any

import soupsieve
from bs4 import BeautifulSoup, Tag

from .extract import PageChanged, _field, extract, valid

COVERAGE = 0.95
MAX_FIELDS = 10  # fields repaired in one go


# --- fingerprints after a good read --------------------------------------------------------------

def _runs(tags) -> tuple:
    """Repeated neighbours once: ("li", "li", "li", "div") → ("li", "div") — so a row more or less
    isn't a layout change."""
    out: list[str] = []
    for t in tags:
        if not out or out[-1] != t:
            out.append(t)
    return tuple(out)


def _structure(el: Tag) -> dict:
    """Scrapling's element_to_dict, from a BeautifulSoup element, without text or values but classes."""
    def attrs(t: Tag) -> dict:
        out = {}
        for k, v in t.attrs.items():
            if k == "class":
                out[k] = " ".join(v) if isinstance(v, list) else v
            elif k == "id" or k.startswith("data-"):
                out[k] = "-"  # the attribute is there; its value could be a name
        return out

    path = tuple(reversed([el.name] + [p.name for p in el.parents if p.name and p.name != "[document]"]))
    d: dict[str, Any] = {"tag": el.name, "attributes": attrs(el), "text": None, "path": path}
    parent = el.parent if isinstance(el.parent, Tag) and el.parent.name != "[document]" else None
    if parent is not None:
        d.update(parent_name=parent.name, parent_attribs=attrs(parent), parent_text=None)
        siblings = _runs(c.name for c in parent.find_all(True, recursive=False) if c is not el)
        if siblings:
            d["siblings"] = siblings
    children = _runs(c.name for c in el.find_all(True, recursive=False))
    if children:
        d["children"] = children
    return d


def _json_safe(d: dict) -> dict:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in d.items()}


def _scrapling(d: dict) -> dict:
    return {k: tuple(v) if isinstance(v, list) else v for k, v in d.items()}


def _selectors(f: dict) -> list[str]:
    s = f.get("select")
    return s if isinstance(s, list) else [s] if s else []


def fingerprints(html: str, spec: dict) -> dict:
    """What each list's items and fields looked like on a page `spec` reads well."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {"lists": {}}
    for list_name, lspec in spec["lists"].items():
        items = soup.select(lspec["item"])
        entry: dict[str, Any] = {"count": len(items), "fields": {}}
        if items:
            entry["item"] = _json_safe(_structure(items[0]))
        for name, f in lspec["fields"].items():
            if "closest" in f:  # read from an ancestor: the item's own fingerprint covers it
                continue
            for css in _selectors(f):
                el = next((hit for it in items for hit in it.select(css)[:1]), None)
                if el is not None:
                    entry["fields"][name] = {"select": css, "element": _json_safe(_structure(el))}
                    break
        out["lists"][list_name] = entry
    return out


def same_layout(a: dict | None, b: dict) -> bool:
    """The same fingerprints, item counts aside (they differ from page to page)."""
    def layout(d: dict) -> dict:
        return {name: {k: v for k, v in entry.items() if k != "count"} for name, entry in d["lists"].items()}
    return a is not None and layout(a) == layout(b)


# --- repair ---------------------------------------------------------------------------------------

def _candidates(found: list) -> list[str]:
    """Selectors made from Scrapling's best matches: their tag with one of their classes, one data
    attribute, or alone. Each is then tried on every item."""
    tags = {el.tag for el in found}
    if len(tags) != 1:
        return []
    tag = tags.pop()
    classes = sorted({c for el in found for c in (el.attrib.get("class") or "").split()})
    datas = sorted({k for el in found for k in el.attrib if k.startswith("data-")})
    return [f"{tag}.{soupsieve.escape(c)}" for c in classes] + [f"{tag}[{d}]" for d in datas] + [tag]


def _try_field(items: list[Tag], f: dict, css: str) -> tuple[tuple, tuple]:
    """A score — (share of items with a valid value, share with exactly one element, fewest
    elements) — and the values, for `f` read with `css`."""
    one = {k: v for k, v in f.items() if k != "select"}
    values, hits = [], []
    for it in items:
        try:
            values.append(_field(it, {**one, "select": css}))
            hits.append(len(it.select(css)))
        except Exception:  # an invalid selector for this parser
            return (0.0, 0.0, 0), ()
    n = len(items) or 1
    ok = sum(1 for v in values if valid(f.get("type"), v)) / n
    return (ok, sum(1 for h in hits if h == 1) / n, -sum(hits)), tuple(str(v) for v in values)


def _best(scored: list[tuple[tuple, str, tuple]]) -> str | None:
    """The best-scoring selector — unless another one scores the same and reads other values."""
    if not scored:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    top = scored[0]
    if any(s[0] == top[0] and s[2] != top[2] for s in scored[1:]):
        return None  # two columns that read as well: don't guess
    return top[1]


def _repair_field(soup: BeautifulSoup, page_doc, lspec: dict, name: str, printed: dict) -> str | None:
    """A new selector for field `name`, or None."""
    items = soup.select(lspec["item"])
    if not items or name not in printed.get("fields", {}):
        return None
    f = lspec["fields"][name]
    found = page_doc.relocate(_scrapling(printed["fields"][name]["element"]), percentage=0)
    scored = []
    for css in _candidates(found):
        if css in _selectors(f):
            continue
        score, values = _try_field(items, f, css)
        if score[0] >= COVERAGE:
            scored.append((score, css, values))
    return _best(scored)


def _repair_items(soup: BeautifulSoup, page_doc, printed: dict) -> str | None:
    """A new item selector for a list that lost its items, or None. Candidates are ranked by how close
    their item count is to the last good read's (pages differ, so it ranks; it doesn't reject); the
    fields are repaired after it, if needed."""
    if "item" not in printed:
        return None
    before = printed["count"]
    found = page_doc.relocate(_scrapling(printed["item"]), percentage=0)
    scored = []
    for css in _candidates(found):
        try:
            n = len(soup.select(css))
        except Exception:
            continue
        if n:
            scored.append(((-abs(n - before),), css, (n,)))
    return _best(scored)


def repair(page: str, html: str, spec: dict, printed: dict | None) -> dict | None:
    """A map that reads `html` again, or None. `printed`: `fingerprints` from the last good read."""
    if not printed:
        return None
    from scrapling.parser import Selector  # only the repair needs it

    soup = BeautifulSoup(html, "html.parser")
    page_doc = Selector(html)
    proposal = copy.deepcopy(spec)
    repaired: set[str] = set()  # lists changed here: they must have items afterwards
    for _ in range(MAX_FIELDS):
        try:
            result = extract(page, html, proposal)
        except PageChanged as e:
            if e.list_name not in printed["lists"]:
                return None
            lspec, was = proposal["lists"][e.list_name], printed["lists"][e.list_name]
            repaired.add(e.list_name)
            if e.field is None:  # the list lost its items
                css = _repair_items(soup, page_doc, was)
                if css is None or css == lspec["item"]:
                    return None
                lspec["item"] = css
                continue
            css = _repair_field(soup, page_doc, lspec, e.field, was)
            if css is None:
                return None
            lspec["fields"][e.field]["select"] = [css] + _selectors(lspec["fields"][e.field])
            continue
        if any(not result[name] for name in repaired):
            return None  # a repaired list left empty: not a repair (others may be empty, e.g. no lineup yet)
        return proposal if proposal != spec else None
    return None


def changes(old: dict, new: dict) -> list[dict]:
    """What a proposal changed: [{list, field (None: the item selector), before, after}]."""
    out = []
    for list_name, lspec in new["lists"].items():
        if lspec["item"] != old["lists"][list_name]["item"]:
            out.append({"list": list_name, "field": None, "before": old["lists"][list_name]["item"], "after": lspec["item"]})
        for name, f in lspec["fields"].items():
            before = old["lists"][list_name]["fields"][name].get("select")
            if f.get("select") != before:
                out.append({"list": list_name, "field": name, "before": before, "after": f.get("select")})
    return out


def counts(page: str, html: str, spec: dict) -> dict:
    """Per list and field, how many items have a value — something to show a person (no values)."""
    result = extract(page, html, spec)
    return {list_name: {"items": len(items),
                        "fields": {name: sum(1 for it in items if it.get(name) not in (None, "", []))
                                   for name in spec["lists"][list_name]["fields"]}}
            for list_name, items in result.items()}

