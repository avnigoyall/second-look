import random
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import radar_insights as ri

T0 = datetime(2025, 11, 10, tzinfo=UTC)


def post(term, ts, author="a", likes=0, text="", reply=None, emb=None):
    return {"term": term, "created_at": ts, "author_hash": author, "like_count": likes, "repost_count": 0,
            "reply_count": 0, "text": text, "reply_parent_uri": reply, "quote_uri": None, "embedding_vultr": emb}


class Flags(unittest.TestCase):
    def test_deal_and_secondhand(self):
        self.assertTrue(ri.flag("Fleece quarter zip only $12.99 (Reg $30) amazon.com/dp/X", "deal"))
        self.assertTrue(ri.flag("selling my old pair on depop, size 8", "secondhand"))
        self.assertTrue(ri.flag("selling my old pair on depop, size 8", "commerce"))
        self.assertFalse(ri.flag("wore my favourite jeans today", "commerce"))
        self.assertTrue(ri.flag("wore my favourite jeans today", "wearing"))
        self.assertTrue(ri.flag("skinny jeans are so over", "backlash"))
        self.assertTrue(ri.flag("bring back capri pants", "revival"))
        self.assertTrue(ri.flag("where can I find a bubble skirt", "desire"))


class CapSample(unittest.TestCase):
    def test_keeps_newest_cap_per_week(self):
        posts = [post("x", T0 + timedelta(hours=i)) for i in range(30)]
        posts += [post("x", T0 + timedelta(days=8, hours=i)) for i in range(5)]
        kept = ri.cap_sample(posts, cap=20)
        self.assertEqual(len(kept), 25)
        week0 = [p for p in kept if ri.week_index(p["created_at"]) == 0]
        self.assertEqual(min(p["created_at"] for p in week0), T0 + timedelta(hours=10))

    def test_velocity(self):
        end = T0 + timedelta(days=7)
        group = [post("x", end - timedelta(hours=i + 1)) for i in range(20)]
        self.assertAlmostEqual(ri.velocity(group, end, cap=20), 1.0)
        self.assertIsNone(ri.velocity(group[:5], end, cap=20))


class BlockMetrics(unittest.TestCase):
    def test_shares_and_newcomers(self):
        bp = [post("x", T0, author=f"u{i % 4}", likes=i, text="only $5 (reg $9)" if i < 5 else "",
                   reply="at://r" if i % 2 else None, emb=[1.0, 0.0]) for i in range(20)]
        m = ri.block_metrics(bp, {"u0", "u1"}, {}, random.Random(1))
        self.assertEqual(m["n"], 20)
        self.assertAlmostEqual(m["deal"], 0.25)
        self.assertAlmostEqual(m["conversation"], 0.5)
        self.assertAlmostEqual(m["newcomer"], 0.5)
        self.assertAlmostEqual(m["top_author_share"], 0.25)
        self.assertEqual(m["communities_per20"], 20)  # no communities known: each author counts as its own

    def test_trajectory(self):
        self.assertAlmostEqual(ri.trajectory([1, 1, 1, 3, 3, 3], 3), 2.0)
        self.assertIsNone(ri.trajectory([1, None, 2], 3))


class Stats(unittest.TestCase):
    def test_mann_whitney_perfect_separation(self):
        r = ri.mann_whitney([5, 6, 7], [1, 2])
        self.assertEqual(r["auc"], 1.0)
        self.assertAlmostEqual(r["p_perm"], 0.2)

    def test_spearman(self):
        self.assertAlmostEqual(ri.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(ri.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)

    def test_cosine(self):
        self.assertAlmostEqual(ri.cosine_distance([1, 0], [0, 1]), 1.0)
        self.assertAlmostEqual(ri.cosine_distance([1, 1], [2, 2]), 0.0)


if __name__ == "__main__":
    unittest.main()
