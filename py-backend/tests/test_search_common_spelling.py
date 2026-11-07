import unittest
from collections import Counter

from app.search_ai.services.search_common import correct_word


class SearchSpellingCorrectionTests(unittest.TestCase):

    def test_valid_english_words_are_preserved(self):
        cases = [
            ("monitor", Counter({"motors": 10})),
            ("measure", Counter({"ensure": 10})),
            ("making", Counter({"packing": 10})),
        ]

        for word, vocabulary in cases:
            with self.subTest(word=word):
                self.assertEqual(correct_word(word, vocabulary), word)

    def test_genuine_catalogue_typo_is_corrected(self):
        vocabulary = Counter({"chair": 10})

        self.assertEqual(
            correct_word("chiar", vocabulary),
            "chair"
        )

    def test_valid_compound_word_is_preserved(self):
        vocabulary = Counter({
            "air": 5,
            "conditioner": 5
        })

        self.assertEqual(
            correct_word("airconditioner", vocabulary),
            "airconditioner"
        )

    def test_unknown_word_without_candidate_is_preserved(self):
        vocabulary = Counter({"chair": 10})

        self.assertEqual(
            correct_word("xyzunknown", vocabulary),
            "xyzunknown"
        )


if __name__ == "__main__":
    unittest.main()