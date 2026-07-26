#!/usr/bin/env python3
"""Offline tests for foreign_boost.py — the local phonetic token-boosting engine.

Run: python -m pytest test_foreign_boost.py   (or: python test_foreign_boost.py)
Pure stdlib + the in-repo islamic_terms table; no key, no network.
"""

import foreign_boost as fb


# ----------------------------- phonetic signature ----------------------------

def test_signature_collapses_variants():
    # j/g, k/c/q, s/z, silent vowels all fold together.
    assert fb.phonetic_signature("juz") == fb.phonetic_signature("juzz")
    assert fb.phonetic_signature("café") == fb.phonetic_signature("cafe")
    assert fb.phonetic_signature("croissant") == fb.phonetic_signature("kroissant")
    assert fb.phonetic_signature("") == ""
    assert fb.phonetic_signature("123") == ""


# ------------------------------ language packs -------------------------------

def test_arabic_pack_reuses_islamic_table():
    pack = fb.language_pack("arabic")
    assert pack is not None
    # "koran" is a curated mishearing of Quran in islamic_terms.
    assert pack.by_mishearing.get("koran") == "Quran"
    # ~250 canonical entries inherited verbatim.
    assert len(pack.entries) > 200


def test_pack_aliases_resolve():
    assert fb.language_pack("ar").name == "arabic"
    assert fb.language_pack("es").name == "spanish"
    assert fb.language_pack("français").name == "french"
    assert fb.language_pack("klingon") is None


def test_available_languages():
    langs = fb.available_languages()
    assert {"arabic", "spanish", "french"} <= set(langs)


# --------------------------- the core boost decision -------------------------

def test_exact_mishearing_of_foreign_word_boosts():
    # "koran" is NOT an English word and IS a known mishearing -> auto-boost.
    r = fb.boost("i recited koran today", ["arabic"])
    assert "//Quran" in r.text
    assert any(b.term == "Quran" and b.tier == "boost" for b in r.applied)


def test_already_correct_spelling_left_alone():
    # "juz"/"amigo" are already the canonical spelling — never re-flag them.
    assert "//" not in fb.boost("the first juz", ["arabic"]).text
    assert "//" not in fb.boost("hey amigo", ["spanish"]).text


def test_plain_english_homophone_untouched_without_context():
    # The headline guard: "just" must stay "just" in ordinary English — no
    # gateway word, normal confidence -> no boost, no offer.
    r = fb.boost("I just left the room", ["arabic"])
    assert r.text == "I just left the room"
    assert not r.changed


def test_ordinary_english_fix_is_not_rewritten_as_fiqh():
    ordinary = fb.boost("Fix this issue", ["arabic"])

    assert fb.strip_flags(ordinary.text) == "Fix this issue"
    assert not ordinary.changed
    assert fb.strip_flags(
        fb.boost("fikh ruling", ["arabic"]).text
    ) == "Fiqh ruling"


def test_homophone_boosts_with_gateway_and_low_confidence():
    # "I memorized the first <just> of the Quran" — "Quran" anchors the context
    # and the acoustic slot for "just" was low-confidence -> boost to //Juz.
    text = "I memorized the first just of the Quran"
    # word tokens: I memorized the first just of the Quran  (index 4 == "just")
    probs = [0.99, 0.95, 0.99, 0.97, 0.40, 0.99, 0.99, 0.93]
    r = fb.boost(text, ["arabic"], word_probs=probs)
    assert "//Juz" in r.text
    assert any(b.term == "Juz" and b.tier == "boost" for b in r.applied)


def test_homophone_only_offered_when_signal_is_weak():
    # Same homophone, gateway present but the slot was HIGH-confidence -> we are
    # not sure enough to auto-apply; offer it as a candidate instead.
    text = "I memorized the first just of the Quran"
    probs = [0.99, 0.95, 0.99, 0.97, 0.92, 0.99, 0.99, 0.93]
    r = fb.boost(text, ["arabic"], word_probs=probs)
    assert "just//Juz" in r.text
    assert any(b.term == "Juz" and b.tier == "offer" for b in r.offered)
    assert "//Juz" not in r.text.replace("just//Juz", "")  # not auto-applied


def test_common_english_term_not_corrupted_in_context():
    # Regression: "the imam saw a shark" must NOT auto-write "Shirk", even though
    # "shark" is a listed mishearing and "imam" opens Islamic context.
    r = fb.boost("the imam saw a shark", ["arabic"])
    # never AUTO-applied (it may be offered as "shark//Shirk" for review)...
    assert not any(b.term == "Shirk" and b.tier == "boost" for b in r.applied)
    assert " //Shirk" not in r.text          # no standalone applied-boost flag
    # ...and the offline render keeps the recognizer's actual word.
    assert fb.strip_flags(r.text) == "the imam saw a shark"


# ------------------------------ other languages ------------------------------

def test_spanish_loanword_boost():
    # canonical "señor" is a common noun -> stays lowercase; flag marks the boost.
    r = fb.boost("buenos dias senor", ["spanish"])
    assert "//señor" in r.text


def test_french_loanword_boost():
    r = fb.boost("i ordered a kwasont", ["french"])
    assert "//croissant" in r.text


def test_multiple_packs_at_once():
    r = fb.boost("koran and kwasont", ["arabic", "french"])
    assert "//Quran" in r.text and "//croissant" in r.text


# ------------------------------ stream rendering -----------------------------

def test_strip_flags_applied_vs_offered():
    # //Term always resolves to Term; heard//Term resolves by `keep`.
    s = "I read //Juz and just//Juz today"
    assert fb.strip_flags(s, keep="heard") == "I read Juz and just today"
    assert fb.strip_flags(s, keep="term") == "I read Juz and Juz today"


def test_idempotent_on_annotated_text():
    # Text already containing '//' is returned untouched (no double-annotation).
    once = fb.boost("i recited koran today", ["arabic"]).text
    twice = fb.boost(once, ["arabic"]).text
    assert once == twice


def test_empty_and_no_pack():
    assert fb.boost("", ["arabic"]).text == ""
    assert fb.boost("hello world", []).text == "hello world"
    assert fb.boost("hello world", ["klingon"]).text == "hello world"


def test_casing_preserved():
    r = fb.boost("Koran is recited", ["arabic"])
    assert "//Quran" in r.text  # canonical already capitalised; title-case heard ok


def test_annotate_convenience_matches_boost_text():
    assert fb.annotate("koran", ["arabic"]) == fb.boost("koran", ["arabic"]).text


def test_mark_only_demotes_boosts_to_offers():
    # CLOUD-AUGMENTED mode: nothing is auto-applied; "koran" becomes an OFFER for
    # the cloud to resolve, not an applied //Quran.
    r = fb.boost("i recited koran today", ["arabic"], mark_only=True)
    assert "koran//Quran" in r.text
    assert "//Quran" not in r.text.replace("koran//Quran", "")  # no applied boost
    assert not r.applied and r.offered


def test_mark_uncertainty_is_offer_only():
    out = fb.mark_uncertainty("i recited koran today", ["arabic"])
    assert "koran//Quran" in out
    # default boost (local-only) WOULD apply it:
    assert "//Quran" in fb.boost("i recited koran today", ["arabic"]).text


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
