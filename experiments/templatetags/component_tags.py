"""Template tags for rendering pluggable question-type components."""
from __future__ import annotations

import logging

from django import template
from django.utils.html import format_html

from experiments.components import (
    BUILTIN_TYPES,
    get_question_component,
    is_question_component,
)

register = template.Library()

logger = logging.getLogger(__name__)


@register.simple_tag(takes_context=True)
def question_widget(context, question):
    """Render a plugin question component's widget (empty for built-in types).

    Used as the ``{% else %}`` fallback in ``survey/_question.html``. On a
    validation-error re-render (a POST) the component receives ``request.POST``
    so it can repopulate the participant's entries.
    """
    if not is_question_component(question.type):
        if question.type in BUILTIN_TYPES:
            return ""
        # A stored plugin type whose component is no longer registered (the
        # plugin app was removed or renamed after authoring). Rendering
        # nothing would strand the participant on a required question with
        # no input — fail visibly instead.
        logger.error(
            "Question %s has type %r but no matching registered component.",
            question.pk,
            question.type,
        )
        return format_html(
            '<p class="question-widget-missing" role="alert">This question '
            "cannot be displayed because its question type ({}) is not "
            "installed on this server. Please contact the study organiser.</p>",
            question.type,
        )
    component = get_question_component(question.type)
    request = context.get("request")
    post = request.POST if request is not None and request.method == "POST" else None
    return component.render(question, post=post)
