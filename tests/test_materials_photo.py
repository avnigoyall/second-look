"""Photo material estimate and item_materials store. Offline: no network, no model download, no torch."""
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

from backend.services import item_materials_store as store_mod
from backend.services import materials_photo as mp
from backend.services.item_materials_store import ItemMaterialsStore, build_doc, item_key, run_items

MODEL, LH, TH = mp.MODEL_ID, mp.labels_config_hash(), mp.thresholds_hash()


class FakeCollection:
    """The subset of a pymongo collection the store uses, with a real unique-index check."""

    def __init__(self, fail_writes: bool = False):
        self.docs: dict[str, dict] = {}
        self.fail_writes = fail_writes
        self.indexes = []
        self.writes = 0

    def create_index(self, field, unique=False, name=None):
        if self.fail_writes:
            raise ServerSelectionTimeoutError("no server")
        self.indexes.append((field, unique, name))

    def replace_one(self, flt, doc, upsert=False):
        if self.fail_writes:
            raise ServerSelectionTimeoutError("no server")
        key = flt["item_key"]
        if key not in self.docs and not upsert:
            return
        if key != doc["item_key"]:
            raise DuplicateKeyError("item_key")
        self.writes += 1
        self.docs[key] = dict(doc)

    def find_one(self, flt):
        return self.docs.get(flt["item_key"])

    def find(self, flt=None, proj=None):
        return [dict(d) for d in self.docs.values()]


def doc_for(key, status="ok", group="wool_knit", model=MODEL, lh=LH, th=TH):
    return build_doc(key=key, source="uniqlo_page", source_url=f"https://example.test/{key}",
                     stated_composition="100% wool", stated_group="wool_knit",
                     estimate={"group": group, "score": .8, "margin": .5, "confidence": "medium"},
                     model_id=model, labels_hash=lh, thresholds_hash=th, run_id="r1", status=status)


class TmpStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fallback = Path(self.tmp.name) / "item_materials.jsonl"
        self.coll = FakeCollection()
        self.store = ItemMaterialsStore(self.coll, self.fallback)

    def tearDown(self):
        self.tmp.cleanup()


class DecideTests(unittest.TestCase):
    def test_confident_top_group_is_medium_never_high(self):
        out = mp.decide({"wool_knit": .95, "cotton_denim": .03, "synthetic": .02})
        self.assertEqual((out["group"], out["confidence"], out["source"]), ("wool_knit", "medium", "photo_estimate"))
        self.assertAlmostEqual(out["margin"], .92)

    def test_middle_band_is_low(self):
        out = mp.decide({"linen": .55, "cotton_denim": .30, "synthetic": .15})
        self.assertEqual((out["group"], out["confidence"]), ("linen", "low"))

    def test_low_score_or_low_margin_is_unknown(self):
        self.assertEqual(mp.decide({"linen": .40, "cotton_denim": .10, "synthetic": .50 - .40})["group"], "unknown")
        out = mp.decide({"linen": .50, "cotton_denim": .40, "synthetic": .10})
        self.assertEqual((out["group"], out["confidence"]), ("unknown", None))

    def test_thresholds_are_labeled_provisional(self):
        self.assertTrue(mp.CONFIG["thresholds"]["provisional"])
        self.assertIn("PROVISIONAL", mp.CONFIG["thresholds"]["_status"])

    def test_every_group_has_several_prompts(self):
        self.assertEqual(set(mp.CONFIG["groups"]), {"cotton_denim", "wool_knit", "synthetic",
                                                    "leather_or_faux", "silk_satin", "linen"})
        for g, spec in mp.CONFIG["groups"].items():
            self.assertGreaterEqual(len(spec["prompts"]), 3, g)


class LineTests(unittest.TestCase):
    def test_single_labeled_line(self):
        est = {"group": "wool_knit", "confidence": "low", "score": .5, "margin": .2}
        self.assertEqual(mp.photo_line(est), "Estimated from photo: likely wool or knit, low confidence")

    def test_no_line_for_unknown_or_missing(self):
        self.assertIsNone(mp.photo_line(None))
        self.assertIsNone(mp.photo_line({"group": "unknown", "confidence": None}))


class GateTests(unittest.TestCase):
    def test_never_called_when_composition_text_exists(self):
        with mock.patch.dict("os.environ", {"MATERIALS_PHOTO_ENABLED": "1"}), \
                mock.patch.object(mp, "estimate_image") as est:
            for item in ({"materials": "100% Cotton"},
                         {"materials": "Body: 80% wool, 20% nylon"},
                         {"materials": "", "description": "Soft knit. 70% Acrylic, 30% Wool."}):
                self.assertIsNone(mp.estimate_for_item(item, b"jpeg-bytes"))
            est.assert_not_called()

    def test_flag_off_by_default_never_calls(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(mp, "estimate_image") as est:
            self.assertFalse(mp.enabled())
            self.assertIsNone(mp.estimate_for_item({}, b"jpeg-bytes"))
            est.assert_not_called()

    def test_called_when_no_composition_and_flag_on(self):
        with mock.patch.dict("os.environ", {"MATERIALS_PHOTO_ENABLED": "1"}), \
                mock.patch.object(mp, "estimate_image", return_value={"group": "linen"}) as est:
            self.assertEqual(mp.estimate_for_item({"materials": None, "description": "Relaxed fit."}, b"x"),
                             {"group": "linen"})
            est.assert_called_once_with(b"x")


class ModelUnavailableTests(unittest.TestCase):
    def test_download_failure_is_non_fatal_unknown_with_error(self):
        fake_torch = types.SimpleNamespace(backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False)))

        def boom(*a, **k):
            raise OSError("We couldn't connect to 'https://huggingface.co'")

        fake_tf = types.SimpleNamespace(CLIPModel=types.SimpleNamespace(from_pretrained=boom),
                                        CLIPProcessor=types.SimpleNamespace(from_pretrained=boom))
        model = mp._Model(mp.MODEL_ID)
        with mock.patch.dict(sys.modules, {"torch": fake_torch, "transformers": fake_tf}), \
                mock.patch.object(mp, "_MODEL", model), self.assertLogs(mp.log, "WARNING"):
            out = mp.estimate_image(b"not-used")
            again = mp.estimate_image(b"not-used")
        self.assertEqual((out["group"], out["status"], out["confidence"]), ("unknown", "model_unavailable", None))
        self.assertIn("OSError: We couldn't connect", out["error"])
        self.assertEqual(again["status"], "model_unavailable")  # failure remembered, no retry loop


class CompositionMappingTests(unittest.TestCase):
    def test_majority_fiber_rule(self):
        cases = {
            "Body: 100% Cotton": "cotton_denim",
            "98% Cotton, 2% Spandex": "cotton_denim",
            "Body: 100% Wool/ Pocket lining: 100% Polyester": "wool_knit",
            "70% Acrylic, 30% Wool": "synthetic",
            "100% Linen": "linen",
            "55% Linen, 45% Cotton": "linen",
            "100% Silk": "silk_satin",
            "50% Cotton, 50% Polyester": "ambiguous",
            "Soft and comfy": None,
        }
        for text, expected in cases.items():
            self.assertEqual(mp.stated_group(text), expected, text)


class KeyTests(unittest.TestCase):
    def test_url_key_ignores_query_fragment_case_and_trailing_slash(self):
        a = item_key("https://www.Uniqlo.com/us/en/products/E123/?colorCode=09#reviews")
        self.assertEqual(a, item_key("https://www.uniqlo.com/us/en/products/E123"))
        self.assertEqual(len(a), 64)

    def test_image_key_when_no_url(self):
        self.assertEqual(item_key(None, b"abc"), item_key(image_bytes=b"abc"))
        self.assertNotEqual(item_key(image_bytes=b"abc"), item_key(image_bytes=b"abd"))
        with self.assertRaises(ValueError):
            item_key()


class StoreTests(TmpStoreTest):
    def test_upsert_is_idempotent(self):
        d = doc_for("k1")
        self.assertEqual(self.store.upsert(d), "db")
        self.assertEqual(self.store.upsert(d), "db")
        self.assertEqual(len(self.coll.docs), 1)
        self.assertIn(("item_key", True, "item_key_unique"), self.coll.indexes)
        changed = doc_for("k1", group="linen")
        self.store.upsert(changed)
        self.assertEqual(len(self.coll.docs), 1)
        self.assertEqual(self.coll.docs["k1"]["estimate"]["group"], "linen")

    def test_document_fields_and_no_image_bytes(self):
        d = doc_for("k1")
        for f in ("item_key", "source", "source_url", "stated_composition", "stated_group", "estimate",
                  "model_id", "labels_config_hash", "thresholds_hash", "scanned_at", "flags", "run_id", "status"):
            self.assertIn(f, d)
        self.assertEqual(set(d["estimate"]), {"group", "score", "margin", "confidence"})
        self.assertEqual(set(d["flags"]), {"hand_labeled", "composition_hidden_for_eval"})
        with self.assertRaises(ValueError):
            self.store.upsert({**d, "image_bytes": b"x"})
        with self.assertRaises(ValueError):
            self.store.upsert({**d, "status": "weird"})

    def test_resume_skips_stored_ok_items_with_same_hashes(self):
        self.store.upsert(doc_for("ok1"))
        self.store.upsert(doc_for("bad1", status="model_unavailable"))
        self.store.upsert(doc_for("old1", th="different"))
        self.assertTrue(self.store.should_skip("ok1", MODEL, LH, TH))
        self.assertFalse(self.store.should_skip("ok1", MODEL, LH, TH, rescan=True))
        self.assertFalse(self.store.should_skip("bad1", MODEL, LH, TH))
        self.assertFalse(self.store.should_skip("old1", MODEL, LH, TH))
        self.assertFalse(self.store.should_skip("ok1", "other/model", LH, TH))
        self.assertFalse(self.store.should_skip("new", MODEL, LH, TH))

    def test_crash_after_5_of_10_resumes_at_item_6(self):
        items = [{"key": f"item{i}"} for i in range(1, 11)]
        seen = []

        def crashing_scan(item):
            if item["key"] == "item6":
                raise RuntimeError("process killed")
            seen.append(item["key"])
            return doc_for(item["key"])

        with self.assertRaises(RuntimeError):
            run_items(self.store, items, crashing_scan, model_id=MODEL, labels_hash=LH, thresholds_hash=TH)
        self.assertEqual(sorted(self.coll.docs), sorted(f"item{i}" for i in range(1, 6)))  # saved before item 6

        second = []

        def scan(item):
            second.append(item["key"])
            return doc_for(item["key"])

        counts = run_items(self.store, items, scan, model_id=MODEL, labels_hash=LH, thresholds_hash=TH)
        self.assertEqual(second, [f"item{i}" for i in range(6, 11)])
        self.assertEqual((counts["skipped"], counts["scanned"]), (5, 5))
        self.assertEqual(len(self.coll.docs), 10)

    def test_jsonl_fallback_syncs_back(self):
        down = ItemMaterialsStore(FakeCollection(fail_writes=True), self.fallback)
        with self.assertLogs(store_mod.log, "WARNING"):
            self.assertEqual(down.upsert(doc_for("a")), "fallback")
            self.assertEqual(down.upsert(doc_for("b")), "fallback")
            self.assertEqual(len(self.fallback.read_text().splitlines()), 2)
            self.assertEqual(down.sync_fallback(), {"synced": 0, "remaining": 2})  # still down: nothing dropped
        self.assertEqual(len(self.fallback.read_text().splitlines()), 2)

        counts = run_items(self.store, [], lambda i: None, model_id=MODEL, labels_hash=LH, thresholds_hash=TH)
        self.assertEqual(counts["synced_from_fallback"], 2)
        self.assertEqual(sorted(self.coll.docs), ["a", "b"])
        scanned_at = self.coll.docs["a"]["scanned_at"]  # round-trips as an aware datetime, not a string
        self.assertIsInstance(scanned_at, datetime)
        self.assertIsNotNone(scanned_at.tzinfo)
        self.assertFalse(self.fallback.exists())

    def test_default_fallback_path_is_gitignored_runs_folder(self):
        self.assertEqual(store_mod.FALLBACK_PATH.parent.name, ".social_runs")
        gitignore = (store_mod.ROOT / ".gitignore").read_text().splitlines()
        self.assertIn(".social_runs/", gitignore)


if __name__ == "__main__":
    unittest.main()
