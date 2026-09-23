"""Alt1-compatible subimage matching on BGRA capture snapshots."""

import os
import logging

import cv2
import numpy as np


def prepare_gpu_image(image):
    """Upload a bound snapshot once; small captures stay on the CPU."""
    if (
        os.environ.get("RUNEKIT_OPENCL") == "0"
        or image.shape[0] * image.shape[1] < 262144
    ):
        return None
    try:
        from runekit.image.gpu_matching import upload_snapshot

        return upload_snapshot(image)
    except (ImportError, RuntimeError):
        logging.getLogger(__name__).debug(
            "GPU matcher unavailable; using CPU", exc_info=True
        )
    return None


def find_subimage(image, needle, x, y, width, height, gpu_image=None):
    """Match Alt1's findSubbuffer tolerance, row order, and 51-result cap.

    Reject candidates using ten color-diverse opaque pixels before checking the
    alpha-weighted RGB distance of every pixel. Work in row tiles to bound
    temporary allocations even when the screen is a single solid color.
    """
    ih, iw = image.shape[:2]
    nh, nw = needle.shape[:2]
    if nw == 0 or nh == 0:
        raise ValueError("Needle must not be empty")
    if x < 0 or y < 0 or width < 0 or height < 0 or x + width > iw or y + height > ih:
        raise ValueError("Search rectangle is outside the bound image")
    columns, rows = width - nw + 1, height - nh + 1
    if columns <= 0 or rows <= 0:
        return []
    if gpu_image is not None and columns * rows >= 262144:
        result = gpu_image.find(needle, x, y, width, height)
        if result is not None:
            return result
    colors = needle[:, :, :3].astype(np.int16)
    opaque = np.argwhere(needle[:, :, 3] == 255)
    anchors = []
    if len(opaque):
        # Adjacent border pixels often have the same color. Repeating that
        # test leaves thousands of false candidates on real game backgrounds.
        # Every opaque pixel is a necessary condition, so reordering the
        # prefilter cannot change the final matches.
        palette = colors[opaque[:, 0], opaque[:, 1]]
        distances = np.full(len(opaque), 1024, dtype=np.int32)
        chosen = 0
        for _ in range(min(10, len(opaque))):
            anchors.append(opaque[chosen])
            distances = np.minimum(
                distances, np.abs(palette - palette[chosen]).sum(axis=1)
            )
            distances[chosen] = -1
            chosen = int(distances.argmax())
        # Prefer a color that is rare on this frame. A distinctive template
        # color can still be common in the game (for example a bright sky).
        sample = np.ascontiguousarray(image[y : y + height : 32, x : x + width : 32])

        def frequency(anchor):
            color = colors[tuple(anchor)]
            low = tuple(int(v) for v in np.maximum(color - 30, 0)) + (0,)
            high = tuple(int(v) for v in np.minimum(color + 30, 255)) + (255,)
            return cv2.countNonZero(cv2.inRange(sample, low, high))

        anchors.sort(key=frequency)
    alpha = needle[:, :, 3].astype(np.float64) / 255
    matches = []
    if len(anchors):
        ay, ax = anchors[0]
        color = colors[ay, ax]
        lower = tuple(int(v) for v in np.maximum(color - 30, 0)) + (0,)
        upper = tuple(int(v) for v in np.minimum(color + 30, 255)) + (255,)
    for start in range(y, y + rows, 64):
        stop = min(start + 64, y + rows)
        if len(anchors):
            ay, ax = anchors[0]
            pixels = image[start + ay : stop + ay, x + ax : x + ax + columns]
            # The per-channel box is a fast SIMD prefilter. Apply the exact
            # summed-distance rule below, including to this first anchor.
            candidates = cv2.findNonZero(cv2.inRange(pixels, lower, upper))
            if candidates is None:
                continue
            cx = candidates[:, 0, 0] + x
            cy = candidates[:, 0, 1] + start
            for ay, ax in anchors:
                if not len(cx):
                    break
                pixels = image[cy + ay, cx + ax, :3].astype(np.int16)
                keep = np.abs(pixels - colors[ay, ax]).sum(axis=1) <= 30
                cy, cx = cy[keep], cx[keep]
        else:
            cy, cx = np.indices((stop - start, columns))
            cy, cx = cy.ravel() + start, cx.ravel() + x
        for py, px in zip(cy, cx):
            pixels = image[py : py + nh, px : px + nw, :3].astype(np.int16)
            distance = np.abs(pixels - colors).sum(axis=2) * alpha
            if np.all(distance <= 30):
                matches.append({"x": int(px), "y": int(py)})
                if len(matches) == 51:
                    return matches
    return matches
