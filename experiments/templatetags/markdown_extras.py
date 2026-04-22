"""Template filters for Markdown rendering."""
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
