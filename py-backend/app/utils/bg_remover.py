"""
Background removal helpers.

The single biggest cost in `rembg.remove()` is building the ONNX inference
session — it loads the model weights from disk (and downloads them on first
use) and compiles the graph. If you don't pass a `session=`, rembg builds a
brand new one on every call, which is why background removal was taking
close to a minute per image.

Fix: build the session exactly once (cached), and warm it up at app startup
so the very first user request isn't the one paying the load cost.

We also downscale oversized inputs before running inference. Brochure page
photos can be several thousand pixels wide; the segmentation model doesn't
need that much resolution to find the subject's edges, and CPU inference
time scales roughly with pixel count. Capping the longest edge cuts
inference time substantially with no visible quality loss for web use.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image
from rembg import new_session, remove

# u2netp is the lightweight distilled version of u2net (~5MB vs ~176MB).
# It loads faster and runs faster on CPU, and quality is indistinguishable
# from u2net for typical product/catalog photos. Switch to "u2net" if you
# need maximum matting accuracy on trickier images (hair, fine fur, etc.)
# and don't mind the extra load/inference time.
MODEL_NAME = "u2netp"

# Cap the longest edge fed into the model. Output keeps this resolution,
# which is plenty for web product images.
MAX_INFERENCE_DIM = 1500


@lru_cache(maxsize=1)
def get_session():
    """Builds (once) and caches the rembg inference session."""
    return new_session(MODEL_NAME)


def warm_up() -> None:
    """Forces the session (and model weights) to load immediately, so the
    cost happens at server startup instead of on a user's first request."""
    get_session()


def _downscale_if_needed(input_bytes: bytes) -> bytes:
    with Image.open(BytesIO(input_bytes)) as img:
        w, h = img.size
        longest = max(w, h)
        if longest <= MAX_INFERENCE_DIM:
            return input_bytes

        scale = MAX_INFERENCE_DIM / longest
        new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
        resized = img.convert("RGBA").resize(new_size, Image.LANCZOS)

        buf = BytesIO()
        resized.save(buf, format="PNG")
        return buf.getvalue()


def remove_background(input_bytes: bytes) -> bytes:
    """Runs background removal using the cached session, on a
    resolution-capped copy of the input for speed."""
    prepared = _downscale_if_needed(input_bytes)
    session = get_session()
    return remove(prepared, session=session)
