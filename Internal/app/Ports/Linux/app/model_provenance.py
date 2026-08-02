"""Immutable upstream revisions for every locally selectable STT model."""

MODEL_REVISIONS = {
    "tiny.en": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
    "base.en": "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
    "small.en": "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
    "medium.en": "a29b04bd15381511a9af671baec01072039215e3",
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
    "distil-large-v3": "c3058b475261292e64a0412df1d2681c06260fab",
    "large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
}


def model_revision(selector):
    """Return the immutable upstream snapshot revision or fail closed."""
    key = str(selector or "").strip().casefold()
    try:
        return MODEL_REVISIONS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported unpinned speech model: {selector!r}") from exc


__all__ = ["MODEL_REVISIONS", "model_revision"]
