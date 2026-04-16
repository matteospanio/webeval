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
