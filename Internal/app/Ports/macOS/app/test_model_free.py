#!/usr/bin/env python3
"""Offline tests for model_free.py — the strict formatting-only no-model pipeline.

Run: python -m pytest test_model_free.py   (or: python test_model_free.py)
Pure stdlib + formatting.py; no key, no network, no model.

The point of these tests is the BOUNDARY: model-free formatting must improve
structure while leaving the user's WORDS and MEANING exactly as transcribed.
"""

import model_free as mf


# ------------------------------ the boundary ---------------------------------

def test_no_foreign_substitution():
    # "koran" must NOT become "Quran" — resolving a foreign term needs context/a model.
    out = mf.process("i recited koran today")
    assert "koran" in out and "Quran" not in out
    assert out == "I recited koran today."


def test_no_homophone_correction():
    # their/there/they're are a model's job — never guessed here.
    assert mf.process("i went their yesterday") == "I went their yesterday."
    assert mf.process("its a nice day") == "Its a nice day."   # no its->it's guess


def test_no_word_substitution_or_meaning_change():
    # A faithful sentence is only formatted; every word survives unchanged.
    out = mf.process("the server returned a null pointer in the auth module")
    assert out == "The server returned a null pointer in the auth module."


def test_no_uncertainty_slashing():
    # No // confidence annotation appears in model-free output.
    assert "//" not in mf.process("i memorised the first juz of the book")


# --------------------------- allowed surface formatting ----------------------

def test_capitalisation_and_terminal():
    assert mf.process("hello world") == "Hello world."
    assert mf.process("i think so") == "I think so."          # standalone i -> I


def test_filled_pause_removal_nonlexical_only():
    # um / uh / er are wordless vocalisations -> removed.
    assert mf.process("um i think uh yes") == "I think yes."


def test_lexical_filler_preserved():
    # "you know" / "like" are REAL WORDS -> kept (faithfulness line).
    assert mf.process("you know i agree") == "You know I agree."
    assert mf.process("it was like really good") == "It was like really good."


def test_split_contraction_rejoin():
    assert mf.process("i do n't know") == "I don't know."
    assert mf.process("they ca n't come") == "They can't come."


def test_number_currency_percent_tightening():
    assert mf.process("it costs 3 . 5 dollars") == "It costs 3.5 dollars."
    assert "$5" in mf.process("it costs $ 5")
    assert "5%" in mf.process("up 5 % today")


def test_ellipsis_normalised():
    # 4+ dots collapse to a single ellipsis (mid-sentence, where it survives;
    # a TRAILING ellipsis legitimately becomes a terminal stop, as in the
    # existing pipeline — artefact cleanup strips trailing punctuation).
    out = mf.process("wait.... what happened")
    assert "..." in out and "...." not in out


def test_conservative_comma():
    # comma before a clause-joining conjunction is a separator, not a word change.
    assert mf.process("i went home and she stayed") == "I went home, and she stayed."


# --------------------------- obvious-artefact removal ------------------------

def test_hallucination_phrase_stripped():
    assert mf.process("thank you for watching") == ""        # never spoken (silence)


def test_repetition_loop_collapsed():
    assert mf.process("go go go go") == "Go."


# ----------------------- smart modes degrade to formatting -------------------

def test_smart_mode_does_not_build_template():
    # mode is IGNORED — no list bullets, no email scaffold, no prompt structure.
    out = mf.process("milk eggs bread", mode="list")
    assert out == "Milk eggs bread."
    assert "- " not in out


def test_degrade_smart_mode_alias():
    out = mf.degrade_smart_mode("make this a prompt about cats", "prompt")
    assert "You are" not in out and "# Task" not in out      # no prompt scaffold
    assert out == "Make this a prompt about cats."


# ------------------------------ shape + manifest -----------------------------

def test_single_output_string():
    # One pass, final text only — never a generator / staged result.
    out = mf.process("hello")
    assert isinstance(out, str)


def test_empty_input():
    assert mf.process("") == ""
    assert mf.process("   ") == ""


def test_capability_manifest():
    cap = mf.capabilities()
    assert cap["semantic"] is False and cap["model_free"] is True
    joined = " ".join(cap["prohibited"]).lower()
    for forbidden in ("substitution", "inference", "homophone", "semantic"):
        assert forbidden in joined
    assert "capitalisation" in " ".join(cap["allowed"]).lower()


if __name__ == "__main__":
    import sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
