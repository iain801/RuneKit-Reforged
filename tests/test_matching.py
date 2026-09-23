import base64
import unittest
import os
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import cv2

from runekit.browser.api import Alt1Api, BoundedRegion
from runekit.browser.utils import ApiPermissionDeniedException
from runekit.image.matching import find_subimage, prepare_gpu_image


def reference(image, needle, x, y, width, height):
    nh, nw = needle.shape[:2]
    out = []
    for py in range(y, y + height - nh + 1):
        for px in range(x, x + width - nw + 1):
            valid = True
            for ny in range(nh):
                for nx in range(nw):
                    delta = sum(
                        abs(int(image[py + ny, px + nx, c]) - int(needle[ny, nx, c]))
                        for c in range(3)
                    )
                    if delta * (int(needle[ny, nx, 3]) / 255) > 30:
                        valid = False
                        break
                if not valid:
                    break
            if valid:
                out.append({"x": px, "y": py})
                if len(out) == 51:
                    return out
    return out


class MatchingTests(unittest.TestCase):
    def test_opencl_can_be_disabled(self):
        image = np.zeros((600, 600, 4), dtype=np.uint8)
        with patch.dict(os.environ, {"RUNEKIT_OPENCL": "0"}):
            self.assertIsNone(prepare_gpu_image(image))

    def test_gpu_failure_falls_back_to_cpu(self):
        image = np.zeros((520, 520, 4), dtype=np.uint8)
        needle = np.full((1, 1, 4), 255, dtype=np.uint8)
        image[300, 300] = needle[0, 0]

        gpu = SimpleNamespace(find=lambda *args: None)
        self.assertEqual(
            find_subimage(image, needle, 0, 0, 520, 520, gpu_image=gpu),
            [{"x": 300, "y": 300}],
        )

    @unittest.skipUnless(
        os.environ.get("RUNEKIT_OPENCL_TESTS") == "1", "requires live OpenCL device"
    )
    def test_opencl_matches_cpu_with_offset_and_tile_boundaries(self):
        rng = np.random.default_rng(42)
        image = rng.integers(0, 256, (600, 640, 4), dtype=np.uint8)
        needle = image[0:4, 0:5].copy()
        needle[:, :, 3] = 255
        for y, x in ((64, 50), (300, 400), (591, 630)):
            image[y : y + 4, x : x + 5] = needle
        with patch.dict(os.environ):
            os.environ.pop("RUNEKIT_OPENCL", None)
            gpu = prepare_gpu_image(image)
        self.assertIsNotNone(gpu)
        expected = find_subimage(image, needle, 7, 11, 628, 585)
        self.assertEqual(
            expected, [{"x": 50, "y": 64}, {"x": 400, "y": 300}, {"x": 630, "y": 591}]
        )
        self.assertEqual(
            find_subimage(image, needle, 7, 11, 628, 585, gpu_image=gpu), expected
        )

    def test_repeated_border_colors_preserve_matches_and_order(self):
        image = np.full((80, 100, 4), [57, 93, 116, 255], dtype=np.uint8)
        needle = image[:11, :17].copy()
        needle[5, 8] = [140, 180, 210, 255]
        needle[7, 9] = [20, 24, 30, 255]
        image[10:21, 15:32] = needle
        image[65:76, 70:87] = needle
        self.assertEqual(
            find_subimage(image, needle, 0, 0, 100, 80),
            [{"x": 15, "y": 10}, {"x": 70, "y": 65}],
        )

    def test_randomized_matches_with_alpha_and_strided_images(self):
        rng = np.random.default_rng(17)
        for alpha in (0, 85, 128, 254, 255, None):
            for _ in range(8):
                image = rng.integers(0, 256, (24, 32, 4), dtype=np.uint8)[::2, ::2]
                needle = image[5:8, 6:9].copy()
                if alpha is not None:
                    needle[:, :, 3] = alpha
                with self.subTest(alpha=alpha):
                    self.assertEqual(
                        find_subimage(image, needle, 2, 3, 12, 8),
                        reference(image, needle, 2, 3, 12, 8),
                    )

    def test_alpha_weighted_tolerance_boundary(self):
        image = np.zeros((1, 4, 4), dtype=np.uint8)
        image[0, :, 0] = [29, 30, 31, 90]
        needle = np.zeros((1, 1, 4), dtype=np.uint8)
        for alpha in (0, 85, 128, 255):
            needle[0, 0, 3] = alpha
            self.assertEqual(
                find_subimage(image, needle, 0, 0, 4, 1),
                reference(image, needle, 0, 0, 4, 1),
            )

    def test_row_order_cap_and_tile_boundary(self):
        image = np.zeros((140, 8, 4), dtype=np.uint8)
        needle = np.zeros((1, 1, 4), dtype=np.uint8)
        self.assertEqual(
            find_subimage(image, needle, 0, 0, 8, 140),
            reference(image, needle, 0, 0, 8, 140),
        )
        image[64, 3, :3] = 255
        needle[:] = 255
        self.assertEqual(
            find_subimage(image, needle, 0, 0, 8, 140), [{"x": 3, "y": 64}]
        )

    def test_invalid_and_oversized_regions(self):
        image = np.zeros((4, 4, 4), dtype=np.uint8)
        self.assertEqual(find_subimage(image, image, 0, 0, 2, 2), [])
        for rect in ((-1, 0, 2, 2), (0, 0, 5, 5), (0, 0, -1, 1)):
            with self.assertRaises(ValueError):
                find_subimage(image, image, *rect)

    def test_rpc_uses_bound_snapshot_and_permission(self):
        image = np.zeros((4, 4, 4), dtype=np.uint8)
        image[2, 1] = [13, 80, 190, 255]
        encoded = base64.b64encode(image[2:3, 1:2].tobytes()).decode()
        api = SimpleNamespace(
            app=SimpleNamespace(has_permission=lambda _: True),
            _bound_regions=[BoundedRegion(image)],
        )
        self.assertEqual(
            Alt1Api.bind_find_subimage(api, 1, encoded, 1, 0, 0, 4, 4),
            [{"x": 1, "y": 2}],
        )
        self.assertEqual(Alt1Api.bind_find_subimage(api, 0, encoded, 1, 0, 0, 4, 4), "")
        api.app.has_permission = lambda _: False
        with self.assertRaises(ApiPermissionDeniedException):
            Alt1Api.bind_find_subimage(api, 1, encoded, 1, 0, 0, 4, 4)
