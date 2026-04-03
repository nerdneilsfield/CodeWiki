#!/usr/bin/env python3
"""
Template utilities for FastAPI applications using Jinja2.
"""

from functools import lru_cache
from typing import Any, Dict

from jinja2 import Environment, BaseLoader, select_autoescape


class StringTemplateLoader(BaseLoader):
    """Custom Jinja2 loader for string templates."""

    def __init__(self, template_string: str):
        self.template_string = template_string

    def get_source(self, environment, template):
        return self.template_string, None, lambda: True


@lru_cache(maxsize=16)
def _compile_template(template_str: str):
    """Compile a Jinja2 template string. Cached by template content."""
    env = Environment(
        loader=StringTemplateLoader(template_str),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("")


def render_template(template: str, context: Dict[str, Any]) -> str:
    """
    Render template using Jinja2.

    Args:
        template: HTML template string with Jinja2 syntax
        context: Dictionary of variables to substitute

    Returns:
        Rendered HTML string
    """
    return _compile_template(template).render(**context)


def render_navigation(module_tree: Dict[str, Any], current_page: str = "") -> str:
    """
    Render navigation HTML from module tree structure.

    Args:
        module_tree: Dictionary representing the module tree
        current_page: Current page filename for highlighting

    Returns:
        HTML string for navigation
    """
    if not module_tree:
        return ""

    nav_template = """
    {%- for section_key, section_data in module_tree.items() %}
    <div class="nav-section">
        <h3>{{ section_key.replace('_', ' ').title() }}</h3>
        {%- if section_data.get('components') %}
        <a href="/{{ section_key }}.md" class="nav-item {{ 'active' if current_page == section_key + '.md' else '' }}">Overview</a>
        {%- endif %}
        {%- if section_data.get('children') %}
        {%- for child_key, child_data in section_data['children'].items() %}
        <div class="nav-subsection">
            <a href="/{{ child_key }}.md" class="nav-item {{ 'active' if current_page == child_key + '.md' else '' }}">{{ child_key.replace('_', ' ').title() }}</a>
        </div>
        {%- endfor %}
        {%- endif %}
    </div>
    {%- endfor %}
    """

    return render_template(nav_template, {"module_tree": module_tree, "current_page": current_page})


def render_job_list(jobs: list) -> str:
    """
    Render job list HTML.

    Args:
        jobs: List of job objects

    Returns:
        HTML string for job list
    """
    if not jobs:
        return ""

    job_list_template = """
    {%- for job in jobs %}
    <div class="job-item">
        <div class="job-header">
            <div class="job-url">{{ job.repo_url }}</div>
            <div class="job-status status-{{ job.status }}">{{ job.status.title() }}</div>
        </div>
        {%- if job.progress %}
        <div class="job-progress">{{ job.progress }}</div>
        {%- endif %}
        {%- if job.status == 'completed' and job.docs_path %}
        <div class="job-actions">
            <a href="/docs/{{ job.job_id }}" class="btn btn-small">View Documentation</a>
        </div>
        {%- endif %}
    </div>
    {%- endfor %}
    """

    return render_template(job_list_template, {"jobs": jobs})
