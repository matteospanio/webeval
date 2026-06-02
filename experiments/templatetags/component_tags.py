"""Template tags for rendering pluggable question-type components."""
from __future__ import annotations

from django import template

from experiments.components import get_question_component, is_question_component

register = template.Library()


@register.simple_tag(takes_context=True)
def question_widget(context, question):
    """Render a plugin question component's widget (empty for built-in types).

    Used as the ``{% else %}`` fallback in ``survey/_question.html``. On a
    validation-error re-render (a POST) the component receives ``request.POST``
    so it can repopulate the participant's entries.
    """
    if not is_question_component(question.type):
        return ""
    component = get_question_component(question.type)
    request = context.get("request")
    post = request.POST if request is not None and request.method == "POST" else None
    return component.render(question, post=post)
