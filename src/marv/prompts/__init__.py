"""Prompt templates system - dynamic prompts with argument substitution."""

from marv.prompts.loader import PromptTemplateLoader
from marv.prompts.parser import substitute_arguments
from marv.prompts.template import PromptTemplate, TemplateSource

__all__ = [
    "PromptTemplate",
    "TemplateSource",
    "PromptTemplateLoader",
    "substitute_arguments",
]
