"""Whole-frame training augmentation.

AGENTS.md: a detection input is always the complete camera frame, in training data too.
So nothing here crops, tiles or masks. Every geometric change maps the full frame INTO
the square canvas (flip, shrink, a perspective tilt whose four corners stay inside the
canvas), and the rest changes pixels only (colour, blur, JPEG, noise, resampling).

    canvas = augment(Image.open(path), size, rng)      # uint8 [size, size, 3]

With rng=None the output is models.letterbox() exactly, the serving geometry.
"""
import io
import math

import numpy as np

from models import PAD_RGB, letterbox


def _homography(source, target):
    """PIL wants the 8 coefficients that send a canvas point to a source-image point."""
    rows, right = [], []
    for (x, y), (u, v) in zip(target, source):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        right += [u, v]
    return np.linalg.solve(np.array(rows, dtype=np.float64), np.array(right, dtype=np.float64))


def _colour(image, rng):
    from PIL import Image, ImageEnhance

    operations = [
        lambda im: ImageEnhance.Brightness(im).enhance(rng.uniform(0.6, 1.45)),
        lambda im: ImageEnhance.Contrast(im).enhance(rng.uniform(0.65, 1.4)),
        lambda im: ImageEnhance.Color(im).enhance(rng.uniform(0.5, 1.5)),
    ]
    for index in rng.permutation(len(operations)):
        if rng.random() < 0.8:
            image = operations[index](image)
    if rng.random() < 0.5:  # gamma and a white-balance tint, per channel
        gamma = rng.uniform(0.7, 1.5)
        gains = rng.uniform(0.9, 1.1, size=3)
        tables = []
        for gain in gains:
            ramp = (np.arange(256) / 255.0) ** gamma * gain
            tables += list(np.clip(ramp * 255 + 0.5, 0, 255).astype(np.uint8))
        image = image.point(tables)
    if rng.random() < 0.05:
        image = image.convert("L").convert("RGB")
    return image


def _blur(image, rng):
    from PIL import ImageFilter

    roll = rng.random()
    if roll < 0.25:
        return image.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.6)))
    if roll < 0.40:  # motion blur along the direction of travel or across it
        kernel = np.zeros((5, 5), dtype=np.float32)
        if rng.random() < 0.6:
            kernel[:, 2] = 1
        else:
            kernel[2, :] = 1
        streak = ImageFilter.Kernel((5, 5), list(kernel.ravel()), scale=5.0)
        image = image.filter(streak)
        return image.filter(streak) if rng.random() < 0.4 else image
    return image


def _jpeg(image, rng):
    from PIL import Image

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=int(rng.integers(30, 92)))
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def augment(image, size, rng=None):
    from PIL import Image

    if rng is None:
        return letterbox(image, size)
    # JPEG can decode at a half or a quarter directly, which is most of the loading cost.
    image.draft("RGB", (size, size))
    image = image.convert("RGB")
    scale = size / max(image.size)
    width = max(1, min(size, math.floor(image.width * scale + 0.5)))
    height = max(1, min(size, math.floor(image.height * scale + 0.5)))
    # The phone, PIL and sharp do not resample alike; neither should training.
    kernel = (Image.Resampling.BICUBIC, Image.Resampling.BILINEAR, Image.Resampling.LANCZOS,
              Image.Resampling.BOX)[int(rng.choice(4, p=(0.55, 0.2, 0.15, 0.1)))]
    if rng.random() < 0.25:  # a lower-resolution camera: fewer real pixels, same frame
        low = rng.uniform(0.45, 0.9)
        image = image.resize((max(8, round(width * low)), max(8, round(height * low))),
                             Image.Resampling.BILINEAR)
    content = image.resize((width, height), kernel)
    content = _colour(content, rng)
    content = _blur(content, rng)
    if rng.random() < 0.5:
        content = _jpeg(content, rng)
    if rng.random() < 0.5:
        content = content.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    left, top = (size - width) // 2, (size - height) // 2
    corners = np.array([[left, top], [left + width, top], [left + width, top + height],
                        [left, top + height]], dtype=np.float64)
    geometric = rng.random() < 0.6
    if geometric:
        centre = np.array([size / 2, size / 2])
        if rng.random() < 0.6:  # tilt: each corner moves a little way towards the inside
            inward = centre - corners
            corners = corners + inward * rng.uniform(0, 0.16, size=(4, 1)) \
                + rng.uniform(-0.02, 0.02, size=(4, 2)) * size
        shrink = rng.uniform(0.7, 1.0) if rng.random() < 0.6 else 1.0
        corners = centre + (corners - centre) * shrink
        # Keep every corner on the canvas: the whole frame stays in view.
        low_corner, high_corner = corners.min(0), corners.max(0)
        span = np.maximum(high_corner - low_corner, 1e-6)
        fit = min(1.0, float((size / span).min()))
        corners = (corners - (low_corner + high_corner) / 2) * fit + (low_corner + high_corner) / 2
        low_corner, high_corner = corners.min(0), corners.max(0)
        room_low, room_high = -low_corner, size - high_corner
        shift = np.where(room_low > room_high, 0.0,
                         rng.uniform(np.minimum(room_low, room_high), np.maximum(room_low, room_high)))
        corners = np.clip(corners + shift, 0, size)
        source = [(0, 0), (width, 0), (width, height), (0, height)]
        canvas = content.transform((size, size), Image.Transform.PERSPECTIVE,
                                   tuple(_homography(source, corners)),
                                   Image.Resampling.BICUBIC, fillcolor=PAD_RGB)
    else:
        canvas = Image.new("RGB", (size, size), PAD_RGB)
        canvas.paste(content, (left, top))
    pixels = np.asarray(canvas, dtype=np.uint8)
    if rng.random() < 0.2:  # sensor noise, on the picture only
        noise = rng.normal(0, rng.uniform(2, 9), size=pixels.shape)
        mask = np.any(pixels != np.array(PAD_RGB, dtype=np.uint8), axis=2, keepdims=True)
        pixels = np.clip(pixels + noise * mask, 0, 255).astype(np.uint8)
    return pixels
