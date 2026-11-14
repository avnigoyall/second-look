"""Offline tests for Part 2 target-item understanding: keyword table, box checks, crop math and the
name-first conflict logic. Gemini is faked. No network, no database.

Run from the project root:  python -m unittest discover tests
"""
import base64
import logging
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from backend import models as m  # noqa: E402
from backend.services import item_understanding as iu  # noqa: E402
from backend.settings import ATTRIBUTES  # noqa: E402

logging.getLogger("secondlook.item_understanding").setLevel(logging.CRITICAL)  # conflicts are expected here


def jpeg(width=200, height=400, color=(255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


def outfit_jpeg() -> bytes:
    """200x400: pink top in the upper half, black jeans in the lower half."""
    img = Image.new("RGB", (200, 400), (240, 240, 240))
    img.paste((240, 160, 180), (50, 40, 150, 180))
    img.paste((20, 20, 20), (50, 200, 150, 380))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def reply(**overrides) -> dict:
    """A well-formed Gemini reply for the pants in an outfit shot."""
    base = {"is_apparel": True, "category": "pants", "color_family": "black", "fabric": "denim",
            "fabric_confidence": "low", "cut": "wide", "style": "streetwear",
            "description": "Black wide-leg pants with a high waist.",
            "target_box_2d": [500, 250, 950, 750], "other_garments_visible": True}
    return {**base, **overrides}


class FakeGemini:
    """Stands in for the genai client. Each generate_content call pops the next scripted reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        return NS(parsed=self.replies.pop(0))


class KeywordTableTest(unittest.TestCase):
    def test_every_category_has_keywords(self):
        self.assertEqual(set(iu.CATEGORY_KEYWORDS), set(ATTRIBUTES["category"]))
        for category, words in iu.CATEGORY_KEYWORDS.items():
            self.assertTrue(words, category)

    def test_names(self):
        cases = {
            "Denim Jacket": "jacket",
            "Light Wash Jean Jacket": "jacket",
            "Cargo Pants": "pants",
            "Tank Top": "tee",
            "Ribbed Tank Tops": "tee",
            "Gray Johnny Fleece Straight Sweatpants": "pants",
            "Carla Ruched Button Up Top": "shirt",
            "Crew Neck Short Sleeve T-Shirt": "tee",
            "Zoe Low Rise Girlfriend Jeans Black": "jeans",
            "Baggy Denim Pants": "jeans",
            "Boot Cut Jeans": "jeans",
            "Pleated Mini Skirt": "skirt",
            "T-Shirt Dress": "dress",
            "Satin Slip Dresses": "dress",
            "Linen Shorts with Belt": "shorts",
            "Blue Snow Wash Full Zip Hoodie": "hoodie",
            "Wool Trench Coat": "coat",
            "Leather Crossbody Bag": "bag",
            "Chunky Sneakers": "shoes",
            "Ballet Flats": "shoes",
            "Polarized Sunglasses": "accessory",
            "Accessories": "accessory",
            "Cable Knit Cardigan": "sweater",
            "Black and White Striped Tee": "tee",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(iu.name_category(name), want)

    def test_no_category(self):
        for name in ["Carla Ruched Top", "Short Sleeve Ruched Top", "Cap Sleeve Top", "Pacsun Gift Card",
                     "Tee and Shorts Set", "", None]:
            with self.subTest(name=name):
                self.assertIsNone(iu.name_category(name))

    def test_whole_words_only(self):
        self.assertIsNone(iu.name_category("Sweatshirt"))  # "shirt" inside a word does not count
        self.assertIsNone(iu.name_category("Overshirt Fleece"))

    def test_breadcrumb_used_when_name_has_no_category(self):
        self.assertEqual(iu.name_category("Carla Top", "Women > Clothing > Jeans"), "jeans")
        self.assertEqual(iu.name_category("Carla Top", "Women / Tops / Tank Tops"), "tee")
        self.assertEqual(iu.name_category("Cargo Pants", "Women > Jeans"), "pants")  # the name wins
        self.assertIsNone(iu.name_category("Carla Top", "Women > Tops"))

    def test_text_values(self):
        self.assertEqual(iu.text_values("color_family", "Washed Black Johnny Fleece Straight Sweatpants"), {"black"})
        self.assertEqual(iu.text_values("color_family", "Heather Grey Hoodie"), {"gray"})
        self.assertEqual(iu.text_values("cut", "Slim Fit Cropped Tee"), {"slim", "cropped"})
        self.assertEqual(iu.text_values("fabric", "100% Cotton"), {"cotton"})
        self.assertEqual(iu.text_values("fabric", "Carla Ruched Button Up Top"), set())


class BoxTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(iu.valid_box([100, 200, 600, 800]), [100, 200, 600, 800])
        self.assertEqual(iu.valid_box([0, 0, 1000, 1000]), [0, 0, 1000, 1000])
        self.assertEqual(iu.valid_box([100.4, 200.6, 600, 800]), [100, 201, 600, 800])

    def test_invalid(self):
        for box in [None, [], [1, 2, 3], [1, 2, 3, 4, 5], "0,0,1,1", [600, 200, 100, 800],
                    [100, 800, 600, 200], [-5, 0, 500, 500], [0, 0, 1001, 500], [100, 100, 110, 600],
                    [100, 100, 600, 120], [0, 0, float("nan"), 500], [True, 0, 500, 500], [0, 0, "500", 500]]:
            with self.subTest(box=box):
                self.assertIsNone(iu.valid_box(box))


class CropTest(unittest.TestCase):
    def test_box_to_pixels_padding(self):
        # box covers y 100..300 and x 50..150 of a 200x400 image, padded by 5% of its size on each side
        self.assertEqual(iu.box_to_pixels([250, 250, 750, 750], 200, 400), (45, 90, 155, 310))

    def test_box_to_pixels_clamped(self):
        self.assertEqual(iu.box_to_pixels([0, 0, 1000, 1000], 200, 400), (0, 0, 200, 400))

    def test_crop_size(self):
        crop = iu.crop_to_box(jpeg(200, 400), [250, 250, 750, 750])
        self.assertEqual(Image.open(io.BytesIO(crop)).size, (110, 220))
        self.assertIsNone(iu.crop_to_box(jpeg(), [750, 250, 250, 750]))

    def test_crop_contains_only_the_box(self):
        crop = iu.crop_to_box(outfit_jpeg(), [100, 250, 450, 750])  # the top
        img = Image.open(io.BytesIO(crop)).convert("RGB")
        r, g, b = img.getpixel((img.width // 2, img.height // 2))
        self.assertGreater(r, 200)  # pink, not the black jeans
        self.assertLess(img.height, 200)

    def test_data_url_is_600px_at_most(self):
        url = iu.to_data_url(iu.crop_to_box(jpeg(1000, 2000), [0, 0, 1000, 1000]))
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        self.assertEqual(max(img.size), 600)


@mock.patch.dict(os.environ, {"GEMINI_MODEL": "fake-model"})
class ConflictTest(unittest.TestCase):
    def run_target(self, replies, name="Carla Ruched Button Up Top", image=None, **kw):
        fake = FakeGemini(replies)
        with mock.patch.object(iu, "_get_client", return_value=fake):
            result = iu.analyze_target(outfit_jpeg() if image is None else image, name, **kw)
        return result, fake

    def test_agreed(self):
        result, fake = self.run_target([reply(category="shirt", color_family="pink", cut="fitted",
                                              target_box_2d=[100, 250, 450, 750])])
        self.assertEqual(result["category_check"], "agreed")
        self.assertEqual((result["category"], result["color_family"]), ("shirt", "pink"))
        self.assertEqual(result["target_box_2d"], [100, 250, 450, 750])
        self.assertTrue(result["other_garments_visible"])
        self.assertTrue(result["target_crop"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(len(fake.calls), 1)

    def test_fixed_by_crop(self):
        result, fake = self.run_target([
            reply(target_box_2d=[100, 250, 450, 750]),  # labeled the pants, boxed the top
            reply(category="shirt", color_family="pink", cut="fitted", style="boho",
                  description="Pink ruched top.", target_box_2d=[0, 0, 1000, 1000], other_garments_visible=False),
        ])
        self.assertEqual(result["category_check"], "fixed_by_crop")
        self.assertEqual((result["category"], result["color_family"], result["style"]), ("shirt", "pink", "boho"))
        self.assertEqual(result["target_box_2d"], [100, 250, 450, 750])  # from the full image, not the crop
        self.assertTrue(result["other_garments_visible"])
        self.assertIsNotNone(result["target_crop"])
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("already cropped around the target item", fake.calls[1][-1])
        retry_image = Image.open(io.BytesIO(fake.calls[1][0].inline_data.data))
        self.assertLess(retry_image.height, 200)  # Gemini saw the crop, not the full outfit

    def test_forced_from_name(self):
        result, fake = self.run_target([reply(), reply()])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(result["category_check"], "forced_from_name")
        self.assertEqual(result["category"], "shirt")
        # nothing in the name supports the image labels of the pants
        self.assertIsNone(result["color_family"])
        self.assertIsNone(result["cut"])
        self.assertEqual(result["style"], "other")
        self.assertEqual((result["fabric"], result["fabric_confidence"]), ("unknown", "low"))
        self.assertIsNone(result["description"])
        self.assertIsNone(result["target_box_2d"])
        self.assertIsNone(result["target_crop"])
        self.assertEqual(iu.attribute_string(result), "shirt.")

    def test_forced_keeps_what_the_text_states(self):
        result, _ = self.run_target([reply(), reply()], name="Washed Black Johnny Fleece Straight Sweatpants",
                                    description="Soft 100% cotton fleece.")
        self.assertEqual(result["category_check"], "agreed")  # sweatpants -> pants, Gemini said pants
        result, _ = self.run_target(
            [reply(category="jacket", color_family="beige", cut="oversized"), reply(category="jacket")],
            name="Washed Black Johnny Fleece Straight Sweatpants", description="Soft 100% cotton fleece.")
        self.assertEqual(result["category_check"], "forced_from_name")
        self.assertEqual(result["category"], "pants")
        self.assertEqual(result["color_family"], "black")  # stated in the name
        self.assertEqual(result["cut"], "straight")  # stated in the name
        self.assertEqual((result["fabric"], result["fabric_confidence"]), ("cotton", "medium"))

    def test_forced_with_two_stated_values_is_null(self):
        result, _ = self.run_target([reply(category="jacket", cut="oversized"), reply(category="jacket")],
                                    name="Straight Leg Sweatpants", description="Relaxed fit.")
        self.assertEqual(result["category"], "pants")
        self.assertIsNone(result["cut"])  # straight and relaxed are both stated, so neither is chosen

    def test_text_only_retry_when_no_box(self):
        result, fake = self.run_target([
            reply(target_box_2d=None),
            reply(category="shirt", color_family="pink", target_box_2d=None),
        ])
        self.assertEqual(result["category_check"], "fixed_by_text")
        self.assertIsNone(result["target_crop"])
        self.assertEqual(len(fake.calls[1]), 1)  # prompt only, no image

    def test_no_retry_when_first_read_was_text_only(self):
        fake = FakeGemini([reply()])
        with mock.patch.object(iu, "_get_client", return_value=fake):
            result = iu.analyze_target(None, "Carla Ruched Button Up Top")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result["category_check"], "forced_from_name")

    def test_invalid_box_is_nulled(self):
        result, _ = self.run_target([reply(category="shirt", target_box_2d=[900, 100, 200, 800])])
        self.assertEqual(result["category_check"], "agreed")
        self.assertIsNone(result["target_box_2d"])
        self.assertIsNone(result["target_crop"])

    def test_no_name_category(self):
        result, fake = self.run_target([reply()], name="Carla Ruched Top")
        self.assertEqual(result["category_check"], "no_name_category")
        self.assertEqual(result["category"], "pants")
        self.assertEqual(len(fake.calls), 1)

    def test_failed_read(self):
        result, _ = self.run_target([{"bad": 1}, {"bad": 2}])
        self.assertEqual(result["status"], "attributes_failed")
        self.assertIsNone(result["category_check"])
        self.assertIsNone(result["target_crop"])

    def test_response_model_accepts_result(self):
        result, _ = self.run_target([reply(category="shirt", target_box_2d=[100, 250, 450, 750])])
        body = m.ItemAttributesResponse(**result, attribute_string=iu.attribute_string(result))
        self.assertEqual(body.category_check, "agreed")
        failed = iu.failed_result()
        m.ItemAttributesResponse(**failed, attribute_string="")

    def test_verdict_doc_fields(self):
        result, _ = self.run_target([reply(category="shirt", target_box_2d=[100, 250, 450, 750])])
        fields = iu.verdict_doc_fields(result)
        self.assertEqual(set(fields), {"attributes", "target_crop"})
        m.Attributes(**fields["attributes"])
        self.assertIsNotNone(fields["target_crop"])
        self.assertEqual(iu.verdict_doc_fields(iu.failed_result()), {"attributes": None, "target_crop": None})


if __name__ == "__main__":
    unittest.main()
