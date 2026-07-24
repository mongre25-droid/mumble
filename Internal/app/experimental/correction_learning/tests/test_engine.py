import unittest

from experimental.correction_learning import analyze_correction


class CorrectionEngineTests(unittest.TestCase):
    def test_one_to_one_replacement(self):
        result = analyze_correction(
            "Please open the Mambo dashboard.",
            "Please open the Mumble dashboard.",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["changes"],
            [
                {
                    "from": "Mambo",
                    "to": "Mumble",
                    "kind": "replacement",
                    "source_word_count": 1,
                }
            ],
        )

    def test_many_to_one_replacement(self):
        result = analyze_correction(
            "Open mum bull preferences now.",
            "Open Mumble preferences now.",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"][0]["from"], "mum bull")
        self.assertEqual(result["changes"][0]["to"], "Mumble")
        self.assertEqual(result["changes"][0]["kind"], "many_to_one")

    def test_mid_sentence_casing_and_acronym_are_supported(self):
        casing = analyze_correction("I opened mumble today", "I opened Mumble today")
        acronym = analyze_correction("Use the api client", "Use the API client")
        self.assertEqual(casing["changes"][0]["kind"], "casing")
        self.assertEqual(acronym["changes"][0]["kind"], "acronym")

    def test_adjacent_acronyms_are_analyzed_as_individual_terms(self):
        result = analyze_correction("Use the api sdk", "Use the API SDK")
        self.assertTrue(result["ok"])
        self.assertEqual(
            [(change["from"], change["to"]) for change in result["changes"]],
            [("api", "API"), ("sdk", "SDK")],
        )

    def test_sentence_capitalization_is_not_learned(self):
        result = analyze_correction("hello world", "Hello world")
        self.assertFalse(result["ok"])
        self.assertEqual(result["rejected"][0]["reason"], "ordinary_sentence_capitalization")

    def test_punctuation_only_edit_is_not_learned(self):
        result = analyze_correction("Hello world", "Hello, world!")
        self.assertFalse(result["ok"])
        self.assertTrue(all(item["reason"] == "punctuation_change" for item in result["rejected"]))

    def test_insertions_and_deletions_are_not_mappings(self):
        inserted = analyze_correction("Send report", "Send the report")
        deleted = analyze_correction("Send the report", "Send report")
        self.assertFalse(inserted["ok"])
        self.assertFalse(deleted["ok"])
        self.assertEqual(inserted["rejected"][0]["reason"], "insertion_or_deletion")

    def test_ordinary_grammar_and_inflection_are_not_learned(self):
        grammar = analyze_correction("It is ready", "It was ready")
        inflection = analyze_correction("They code daily", "They coded daily")
        irregular = analyze_correction("We run daily", "We ran daily")
        self.assertFalse(grammar["ok"])
        self.assertFalse(inflection["ok"])
        self.assertFalse(irregular["ok"])
        self.assertEqual(grammar["rejected"][0]["reason"], "ordinary_grammar_change")
        self.assertIn(
            inflection["rejected"][0]["reason"],
            {"ordinary_inflection", "ordinary_grammar_change"},
        )

    def test_ordinary_content_rewrite_is_not_a_global_mapping(self):
        result = analyze_correction("It is fast today", "It is quick today")
        branded = analyze_correction("Use C++ today", "Use Rust today")
        self.assertFalse(result["ok"])
        self.assertFalse(branded["ok"])
        self.assertEqual(result["rejected"][0]["reason"], "ordinary_content_rewrite")

    def test_contraction_punctuation_and_uppercase_emphasis_are_rejected(self):
        contraction = analyze_correction("dont stop", "don't stop")
        emphasis = analyze_correction("Please say hello now", "Please say HELLO now")
        self.assertFalse(contraction["ok"])
        self.assertFalse(emphasis["ok"])
        self.assertEqual(contraction["rejected"][0]["reason"], "punctuation_change")
        self.assertEqual(emphasis["rejected"][0]["reason"], "ordinary_emphasis_change")

    def test_close_lowercase_spelling_correction_remains_supported(self):
        result = analyze_correction("The colour changed", "The color changed")
        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"][0]["to"], "color")

    def test_whole_prose_rewrite_is_rejected(self):
        result = analyze_correction(
            "Please send the quarterly report to Jordan before lunch today",
            "Could you email Jordan our financial summary sometime this afternoon",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["rejected"], [{"reason": "whole_prose_rewrite", "count": 1}])

    def test_useful_term_survives_an_unrelated_punctuation_edit(self):
        result = analyze_correction(
            "Open mum bull settings",
            "Open Mumble settings!",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["rejected"], [{"reason": "punctuation_change", "count": 1}])

    def test_technology_term_punctuation_is_part_of_the_term(self):
        result = analyze_correction(
            "Compile with see plus plus today",
            "Compile with C++ today",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"][0]["to"], "C++")

    def test_invalid_and_unchanged_inputs_are_safe(self):
        self.assertFalse(analyze_correction("", "word")["ok"])
        self.assertFalse(analyze_correction(None, "word")["ok"])
        self.assertFalse(analyze_correction("same words", "same words")["ok"])


if __name__ == "__main__":
    unittest.main()
