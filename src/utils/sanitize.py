"""
Tiny text-sanitization helpers shared across the project.

Banner (and RateMyProfessors) hand us strings with HTML entities baked in —
"Math &amp; Technology Center", "Children&#39;s Lit". Those entities are an
artifact of how the source pages are encoded, not something we ever want to show
a student or feed to the model. unescape_html() decodes them once, anywhere in a
nested course structure.
"""
from __future__ import annotations

import html


def unescape_html(obj):
    """
    Recursively decode HTML entities in every string within `obj`.

    Walks dicts and lists; decodes strings; leaves numbers, booleans, and None
    untouched. Returns a structure of the same shape (new containers, same scalars).
    html.unescape is a no-op on already-clean text, so this is safe to apply more
    than once.
    """
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, dict):
        return {key: unescape_html(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [unescape_html(item) for item in obj]
    return obj
