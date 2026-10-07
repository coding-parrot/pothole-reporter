"""The encoders compared, their pooling, and the one whole-frame preprocessing.

AGENTS.md forbids cropping, tiling or masking a detection input, so every model sees
the complete frame: it is scaled to fit a square (long edge = input size) and the
remainder is padded with the ImageNet mean, which is zero after normalisation.
"""
import math

import numpy as np
import torch
from torch import nn

MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
PAD_RGB = tuple(round(255 * value) for value in MEAN)  # (124, 116, 104)

# name: (timm id, feature kind). Licences are recorded in MODEL_CARD.md.
ENCODERS = {
    "dinov2_s14": ("vit_small_patch14_dinov2.lvd142m", "tokens"),
    "efficientnet_b0": ("efficientnet_b0.ra_in1k", "map"),
    "mobilenetv3_l": ("mobilenetv3_large_100.ra_in1k", "map"),
}


def default_device():
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def letterbox(image, size):
    """PIL image -> size x size RGB uint8 array holding the whole frame."""
    from PIL import Image

    image = image.convert("RGB")
    scale = size / max(image.size)
    width = max(1, min(size, math.floor(image.width * scale + 0.5)))
    height = max(1, min(size, math.floor(image.height * scale + 0.5)))
    resized = image.resize((width, height), Image.Resampling.BICUBIC)
    canvas = Image.new("RGB", (size, size), PAD_RGB)
    canvas.paste(resized, ((size - width) // 2, (size - height) // 2))
    return np.asarray(canvas, dtype=np.uint8)


class Encoder(nn.Module):
    """uint8 NHWC frames in, one pooled feature vector per frame out."""

    def __init__(self, name, size, pretrained=True):
        super().__init__()
        import timm

        timm_id, self.kind = ENCODERS[name]
        extra = {"img_size": size} if self.kind == "tokens" else {}
        self.body = timm.create_model(timm_id, pretrained=pretrained, num_classes=0, **extra)
        self.prefix = getattr(self.body, "num_prefix_tokens", 0)
        self.register_buffer("mean", torch.tensor(MEAN).view(1, 3, 1, 1) * 255)
        self.register_buffer("std", torch.tensor(STD).view(1, 3, 1, 1) * 255)

    def forward(self, frames):
        x = (frames.permute(0, 3, 1, 2).float() - self.mean) / self.std
        features = self.body.forward_features(x)
        if self.kind == "tokens":
            patches = features[:, self.prefix:]
            # Class token, mean and max over the patch tokens. The max keeps a small,
            # far pothole from being averaged away by the rest of the frame.
            return torch.cat([features[:, 0], patches.mean(1), patches.amax(1)], dim=1)
        return torch.cat([features.mean((2, 3)), features.amax((2, 3))], dim=1)


class Head(nn.Module):
    """Standardise, then a linear layer or one hidden layer. Output is a logit."""

    def __init__(self, width, hidden=0, dropout=0.0):
        super().__init__()
        self.register_buffer("centre", torch.zeros(width))
        self.register_buffer("scale", torch.ones(width))
        layers = ([nn.Linear(width, 1)] if not hidden else
                  [nn.Linear(width, hidden), nn.GELU(), nn.Dropout(dropout), nn.Linear(hidden, 1)])
        self.layers = nn.Sequential(*layers)

    def forward(self, features):
        return self.layers((features - self.centre) / self.scale).squeeze(-1)


class Screen(nn.Module):
    """The served model: letterboxed uint8 frame in, probability of damage out."""

    def __init__(self, encoder, head):
        super().__init__()
        self.encoder, self.head = encoder, head

    def forward(self, frames):
        return torch.sigmoid(self.head(self.encoder(frames)))
