"""Tests for the offline everyday-life post text generator."""

import random
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import post_generator  # noqa: E402
from post_generator import PostTextGenerator, time_bucket  # noqa: E402


def generator_at(hour, weekday_date=(2026, 11, 25), seed=1):
    moment = datetime(*weekday_date, hour, 0)
    return PostTextGenerator(rng=random.Random(seed), clock=lambda: moment)


class TimeBucketTests(unittest.TestCase):
    def test_buckets(self):
        cases = {
            4: "pagi", 9: "pagi", 10: "siang", 14: "siang",
            15: "sore", 17: "sore", 18: "malam", 23: "malam", 0: "malam", 3: "malam",
        }
        for hour, expected in cases.items():
            self.assertEqual(time_bucket(datetime(2026, 1, 1, hour)), expected, hour)


class PostTextGeneratorTests(unittest.TestCase):
    def test_texts_are_non_empty_sentences(self):
        generator = generator_at(9)
        for text in generator.generate_many(post_generator.MAX_BATCH):
            first_line = text.split("\n")[0]
            self.assertTrue(first_line[0].isupper(), text)
            self.assertLessEqual(len(text), 280)
            self.assertNotIn("~", text)
            self.assertNotIn("  ", first_line)

    def test_batch_has_no_duplicates(self):
        texts = generator_at(13).generate_many(post_generator.MAX_BATCH)
        self.assertEqual(len(texts), len(set(texts)))

    def test_batch_size_is_clamped(self):
        generator = generator_at(13)
        self.assertEqual(len(generator.generate_many(0)), 1)
        self.assertEqual(
            len(generator.generate_many(999)), post_generator.MAX_BATCH
        )

    def test_morning_texts_never_use_evening_activities(self):
        evening = {
            post_generator._sentence(a.lstrip("~")).rstrip(".")
            for a in post_generator._ACTIVITIES["malam"]
        }
        for text in generator_at(7, seed=3).generate_many(50):
            for activity in evening:
                self.assertNotIn(activity, text)

    def test_tired_activities_get_tired_feelings(self):
        happy_only = set(post_generator._FEELINGS["senang"]) - set(
            post_generator._FEELINGS["lelah"]
        )
        tired = [
            post_generator._sentence(a.lstrip("~"))
            for bucket in post_generator._ACTIVITIES.values()
            for a in bucket
            if a.startswith("~")
        ]
        for seed in range(30):
            for text in generator_at(13, seed=seed).generate_many(20):
                if any(text.startswith(activity) for activity in tired):
                    for feeling in happy_only:
                        self.assertNotIn(feeling, text)

    def test_day_lines_follow_the_weekday(self):
        # 2026-11-27 is a Friday, 2026-11-25 a Wednesday (no day line)
        friday = " ".join(generator_at(9, (2026, 11, 27), seed=5).generate_many(50))
        self.assertTrue(
            any(line in friday for line in post_generator._DAY_LINES[4])
        )
        wednesday = " ".join(generator_at(9, (2026, 11, 25), seed=5).generate_many(50))
        for lines in post_generator._DAY_LINES.values():
            for line in lines:
                self.assertNotIn(line, wednesday)


if __name__ == "__main__":
    unittest.main()
