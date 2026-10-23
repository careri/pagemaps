"""pagemaps: read HTML pages with JSON maps, and repair a map when the page's markup changes."""

from .extract import TYPES, PageChanged, extract, text, valid, validate
from .heal import changes, counts, fingerprints, repair, same_layout

__all__ = ["TYPES", "PageChanged", "extract", "text", "valid", "validate",
           "changes", "counts", "fingerprints", "repair", "same_layout"]
__version__ = "0.1.1"
