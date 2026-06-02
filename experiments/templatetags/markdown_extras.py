"""Template filters for Markdown rendering."""
import json

from django import template
from django.utils.safestring import mark_safe

import markdown as md

register = template.Library()


@register.filter(name="markdown")
def markdown_filter(value):
    """Render a string as Markdown HTML."""
    if not value:
        return ""
    return mark_safe(md.markdown(str(value), extensions=["extra", "sane_lists"]))


@register.filter(name="markdown_inline")
def markdown_inline_filter(value):
    """Render Markdown without the outer <p> wrapper so the result is safe
    to embed inside phrasing contexts like <span> or <legend>."""
    if not value:
        return ""
    html = md.markdown(str(value), extensions=["extra", "sane_lists"]).strip()
    if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
        html = html[3:-4]
    return mark_safe(html)


@register.filter(name="get_item")
def get_item(value, key):
    """Look up ``value[key]`` in a dict, tolerant of missing keys/None.

    Used to repopulate matrix/ranking widgets after a validation error, where
    the per-row/per-item submitted values are stored in a dict keyed by index.
    """
    if isinstance(value, dict):
        if key in value:
            return value[key]
        return value.get(str(key), "")
    return ""


@register.filter(name="as_json")
def as_json(value):
    """Serialise a value to a JSON string.

    Deliberately *not* marked safe: Django auto-escapes the result, so it embeds
    safely inside an HTML attribute (e.g. ``data-visible-if``). The browser
    decodes the entities, leaving valid JSON for ``JSON.parse``.
    """
    return json.dumps(value)
