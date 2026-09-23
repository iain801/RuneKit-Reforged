from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from runekit.game.instance import ImageType


def np_crop(image: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    img_height, img_width = image.shape[:2]
    w = img_width if w is None else w
    h = img_height if h is None else h
    if w < 0 or h < 0:
        raise ValueError("Capture dimensions cannot be negative")
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(img_width, x + w), min(img_height, y + h)
    if x1 == x and y1 == y and x2 == x + w and y2 == y + h:
        return image[y1:y2, x1:x2]
    out = np.zeros((h, w, *image.shape[2:]), dtype=image.dtype)
    if x2 > x1 and y2 > y1:
        out[y1 - y : y2 - y, x1 - x : x2 - x] = image[y1:y2, x1:x2]
    return out


def np_save_image(image: np.ndarray, out: str):
    from PIL import Image

    size = (image.shape[1], image.shape[0])
    if len(image.shape) == 2:
        # Grayscale
        Image.frombuffer("L", size, image.tobytes(), "raw", "L", 0, 1).save(out)
    elif image.shape[2] == 3:
        # RGB
        Image.frombuffer(
            "RGB", size, image[:, :, ::-1].tobytes(), "raw", "RGB", 0, 1
        ).save(out)
    else:
        # RGBA
        Image.frombuffer(
            "RGBA", size, image[:, :, [2, 1, 0, 3]].tobytes(), "raw", "RGBA", 0, 1
        ).save(out)


def ensure_np_image(image: "ImageType") -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image

    if image.mode == "RGB":
        image = image.convert("RGBA")

    out = np.array(image)
    out = out[:, :, [2, 1, 0, 3]]  # B G R A
    return out
