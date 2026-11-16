"""Sandboxed rendering for user-managed email signature templates."""
from collections.abc import Mapping
from typing import Any
import re

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

_environment = SandboxedEnvironment(autoescape=True)
_environment.globals.clear()
_environment.filters.pop("safe", None)
_GROUP_PLACEHOLDER = re.compile(r"{{\s*group:([a-z0-9-]{1,64})\s*}}")


def render_signature_template(template_html: str, values: Mapping[str, Any]) -> str:
    if not isinstance(template_html, str):
        raise TypeError("template_html must be a string")
    return _environment.from_string(template_html).render(**dict(values))


def prepare_group_placeholders(template_html: str) -> tuple[str, dict[str, str]]:
    slots: dict[str, str] = {}
    def replace(match: re.Match[str]) -> str:
        slug = match.group(1)
        key = slots.setdefault(slug, f"group_slot_{len(slots)}")
        return "{{ " + key + " }}"
    return _GROUP_PLACEHOLDER.sub(replace, template_html), slots


def strip_group_placeholders(template_html: str) -> str:
    return _GROUP_PLACEHOLDER.sub("", template_html)
