from prompt_constitution import TEMPLATE_METADATA
from prompt_template_registry import RESEARCH_REFERENCES, template_metadata


def test_prompt_template_provenance_is_explicit_and_original():
    meta = template_metadata()
    assert meta["id"] == "mumble.prompt-architect"
    assert meta["version"] == TEMPLATE_METADATA.version
    assert meta["copied_text"] is False
    assert meta["output_contract"]
    assert {item["licence"] for item in RESEARCH_REFERENCES} == {"MIT"}


def test_unknown_prompt_template_is_rejected():
    try:
        template_metadata("unknown")
    except KeyError:
        return
    raise AssertionError("unknown prompt template should raise KeyError")
