"""Opt-in tests for the complete OpenCL search pipeline."""

import os
import unittest
from unittest.mock import patch

import numpy as np

from runekit.image.matching import find_subimage


@unittest.skipUnless(
    os.environ.get("RUNEKIT_OPENCL_TESTS") == "1", "requires live OpenCL device"
)
class GpuMatchingTests(unittest.TestCase):
    def snapshot(self, image):
        from runekit.image.gpu_matching import upload_snapshot

        return upload_snapshot(image)

    def test_alpha_boundaries_against_cpu_for_all_alpha_values(self):
        distances = np.arange(765, -1, -1)
        image = np.zeros((1, 766, 4), dtype=np.uint8)
        for channel in range(3):
            image[0, :, channel] = np.clip(distances - 255 * channel, 0, 255)
        snapshot = self.snapshot(image)
        needle = np.zeros((1, 1, 4), dtype=np.uint8)
        for alpha in range(256):
            needle[0, 0, 3] = alpha
            with self.subTest(alpha=alpha):
                self.assertEqual(
                    snapshot.find(needle, 0, 0, 766, 1),
                    find_subimage(image, needle, 0, 0, 766, 1),
                )

    def test_randomized_opaque_translucent_and_transparent_templates(self):
        rng = np.random.default_rng(91)
        for alpha in (0, 128, 255, None):
            for _ in range(10):
                image = rng.integers(0, 256, (35, 53, 4), dtype=np.uint8)
                needle = image[11:15, 8:13].copy()
                if alpha is not None:
                    needle[:, :, 3] = alpha
                with self.subTest(alpha=alpha):
                    self.assertEqual(
                        self.snapshot(image).find(needle, 3, 7, 47, 25),
                        find_subimage(image, needle, 3, 7, 47, 25),
                    )

    def test_row_order_across_workgroups_and_result_cap(self):
        image = np.zeros((20, 1024, 4), dtype=np.uint8)
        image.reshape(-1, 4)[::257] = 255
        needle = np.full((1, 1, 4), 255, dtype=np.uint8)
        expected = find_subimage(image, needle, 0, 0, 1024, 20)
        self.assertEqual(len(expected), 51)
        self.assertEqual(self.snapshot(image).find(needle, 0, 0, 1024, 20), expected)

    def test_device_error_disables_snapshot_and_returns_cpu_fallback(self):
        import pyopencl as cl

        snapshot = self.snapshot(np.zeros((2, 2, 4), dtype=np.uint8))
        with patch.object(
            snapshot.engine,
            "find",
            side_effect=cl.RuntimeError("simulated device loss"),
        ) as call:
            with self.assertLogs("runekit.image.gpu_matching", level="WARNING"):
                self.assertIsNone(
                    snapshot.find(np.zeros((1, 1, 4), dtype=np.uint8), 0, 0, 2, 2)
                )
            self.assertIsNone(
                snapshot.find(np.zeros((1, 1, 4), dtype=np.uint8), 0, 0, 2, 2)
            )
            self.assertEqual(call.call_count, 1)
