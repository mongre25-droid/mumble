"""Public runtime-model revision contract."""

import pytest

from model_provenance import model_revision


@pytest.mark.parametrize(
    ("selector", "revision"),
    (
        ("tiny.en", "0d3d19a32d3338f10357c0889762bd8d64bbdeba"),
        ("small.en", "d1d751a5f8271d482d14ca55d9e2deeebbae577f"),
        ("distil-large-v3", "c3058b475261292e64a0412df1d2681c06260fab"),
        ("large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"),
    ),
)
def test_supported_runtime_models_resolve_to_immutable_revisions(selector, revision):
    assert model_revision(selector) == revision


def test_unknown_runtime_model_fails_closed():
    with pytest.raises(ValueError, match="unsupported.*model"):
        model_revision("moving-main")
