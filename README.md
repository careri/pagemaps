# pagemaps

Self-healing HTML scraping: describe a page's fields in a JSON map, and when the site's markup
changes, pagemaps finds them again and proposes a fixed map.

## Reading a page

A map names a page's lists and fields and says where they are:

```json
{
  "page": "example.team",
  "lists": {
    "players": {
      "item": "ul.roster > li",
      "fields": {
        "number": {"type": "number", "select": "span.num", "text": true, "pattern": "(\\d+)"},
        "name":   {"type": "name", "select": ["span.name", "span.full-name"], "text": true, "required": true},
        "link":   {"type": "id", "select": "a", "attr": "href", "pattern": "/players/(\\d+)"}
      },
      "expect": {"minItems": 1}
    }
  }
}
```

```python
from pagemaps import extract, PageChanged

data = extract("example.team", html, spec)   # {"players": [{"number": "7", "name": "…", "link": "123"}, …]}
```

Field options: `select` (a CSS selector inside the item, or a list tried in order), `text`, `attr`,
`pattern`, `many`, `all`, `map`, `exists`, `closest`, `required` and `type` — see `extract.py`.
A required field missing in any item, or a list shorter than `minItems`, raises `PageChanged`
naming the map, list and field.

## Repairing a map

After a good read, keep the page's fingerprints; after a failed one, ask for a repair:

```python
from pagemaps import fingerprints, repair, changes, counts

printed = fingerprints(html, spec)          # store this (JSON) with the map

try:
    extract("example.team", new_html, spec)
except PageChanged:
    proposal = repair("example.team", new_html, spec, printed)
    if proposal:
        print(changes(spec, proposal))      # [{"list": "players", "field": "name", "before": …, "after": […]}]
        print(counts("example.team", new_html, proposal))
```

- **Fingerprints hold structure only** — tag, class names, the path of tags, the parent, the
  neighbours' tags — **never text or attribute values other than classes**, so they can be stored
  even when the pages hold personal data.
- The repair lets [Scrapling](https://github.com/D4Vinci/Scrapling) score every element of the new
  page against the fingerprint, makes selectors from its best matches (tag + class, data attribute),
  and keeps the one that gives a valid value (`type`) in at least 95 % of the items — preferring one
  element per item. Two candidates that read equally well but differently: no guess, no proposal.
- A proposal puts the new selector first in the field's `select` list and keeps the old one as a
  fallback. It is never applied by itself: show `changes` and `counts` to a person and let them save it.
- A list that lost its items gets a new item selector the same way.

## License

BSD 3-Clause — see `LICENSE`. Its dependencies: Scrapling, lxml, cssselect, w3lib (BSD-3-Clause), BeautifulSoup and soupsieve (MIT), orjson (MPL-2.0 and Apache-2.0 or MIT), tld (MPL-1.1 or GPL-2.0 or LGPL-2.1).
