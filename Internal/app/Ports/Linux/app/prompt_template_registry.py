#!/usr/bin/env python3
"""Provenance metadata for Mumble's prompt-shaping instructions.

Prompt text is executable product behaviour.  Keeping its authorship, version,
and licence posture explicit prevents a future contributor from pasting an
incompatible third-party pattern into the runtime without review.

The current prompt constitution is original Mumble material.  External projects
listed in ``RESEARCH_REFERENCES`` informed the architecture only; none of their
prompt text is copied into Mumble.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplate:
    template_id: str
    version: str
    modes: tuple
    source: str
    licence: str
    copied_text: bool
    output_contract: str


PROMPT_ARCHITECT = PromptTemplate(
    template_id="mumble.prompt-architect",
    version="5",
    modes=("prompt",),
    source="Mumble original",
    licence="Project licence",
    copied_text=False,
    output_contract="Return only the finished, ready-to-use prompt.",
)


RESEARCH_REFERENCES = (
    {
        "name": "Fabric improve_prompt pattern",
        "url": (
            "https://github.com/danielmiessler/Fabric/"
            "blob/main/data/patterns/improve_prompt/system.md"
        ),
        "licence": "MIT",
        "use": "Architecture reference only; no pattern text copied.",
    },
    {
        "name": "Microsoft promptflow",
        "url": "https://github.com/microsoft/promptflow",
        "licence": "MIT",
        "use": "Versioning and evaluation reference; no code copied.",
    },
)


def template_metadata(template_id="mumble.prompt-architect"):
    """Return serialisable provenance for diagnostics and future evaluators."""
    if template_id != PROMPT_ARCHITECT.template_id:
        raise KeyError(template_id)
    return {
        "id": PROMPT_ARCHITECT.template_id,
        "version": PROMPT_ARCHITECT.version,
        "modes": list(PROMPT_ARCHITECT.modes),
        "source": PROMPT_ARCHITECT.source,
        "licence": PROMPT_ARCHITECT.licence,
        "copied_text": PROMPT_ARCHITECT.copied_text,
        "output_contract": PROMPT_ARCHITECT.output_contract,
    }
