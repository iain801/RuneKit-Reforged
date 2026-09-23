"""Pixel layout regressions and profiling startup configuration."""

import os
import unittest
from unittest.mock import patch

import numpy as np
from click.testing import CliRunner

from runekit.browser.utils import image_to_stream
from runekit.image.np_utils import np_crop
from runekit.main import main


class PixelTransferTests(unittest.TestCase):
    def test_rgba_bytes_for_full_strided_padded_and_empty_regions(self):
        image = np.arange(7 * 9 * 4, dtype=np.uint8).reshape(7, 9, 4)
        original = image.copy()
        for source in (image, image[::2, ::2]):
            for rect in (
                (0, 0, None, None),
                (1, 2, 3, 2),
                (-2, -1, 5, 4),
                (20, 20, 2, 3),
                (0, 0, 0, 4),
                (0, 0, 4, 0),
            ):
                with self.subTest(shape=source.shape, rect=rect):
                    cropped = np_crop(source, *rect)
                    expected = cropped[:, :, [2, 1, 0, 3]].tobytes()
                    self.assertEqual(
                        image_to_stream(source, *rect, mode="rgba"), expected
                    )
                    self.assertEqual(
                        image_to_stream(source, *rect, mode="bgra"), cropped.tobytes()
                    )
        np.testing.assert_array_equal(image, original)


class ProfilingOptionTests(unittest.TestCase):
    def test_debugger_configured_before_webengine_initialization(self):
        observed = []

        def stop_before_gui():
            observed.append(os.environ.get("QTWEBENGINE_REMOTE_DEBUGGING"))
            raise RuntimeError("stop before GUI")

        with (
            patch.dict(os.environ),
            patch("runekit.main.browser.init", side_effect=stop_before_gui),
        ):
            result = CliRunner().invoke(main, ["--devtools-port", "9222"])
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertEqual(observed, ["127.0.0.1:9222"])

    def test_invalid_ports_rejected(self):
        for port in ("0", "65536", "abc"):
            self.assertEqual(
                CliRunner().invoke(main, ["--devtools-port", port]).exit_code, 2
            )
