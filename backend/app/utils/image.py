import math
from collections.abc import Sequence
from typing import Literal, NamedTuple

import numpy as np
import numpy.typing as npt
import simplejpeg

from app.core.config import Crop


class Box(NamedTuple):
    """Detection box, coordinates 0-1 relative to the cropped image."""

    cls: Literal["hand", "dirt"]
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float


Image = npt.NDArray[np.uint8]  # H x W x 3 RGB
LevelMap = npt.NDArray[np.uint8]  # H x W HandCleanEngine levels: 0 outside, 1-5 residue, 6 clean, 7 unusable
Palette = npt.NDArray[np.uint8]  # 8 x 3 RGB, row = level; only rows 1-5 are drawn
Hand = Sequence[tuple[float, float]]  # 21 MediaPipe landmarks, (x, y) 0-1 of the image

# MediaPipe Hands bones (mediapipe.solutions.hands.HAND_CONNECTIONS, as in handclean_engine.hand_mask.BONES)
BONES = (
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)
HAND_COLORS: tuple[tuple[int, int, int], ...] = ((0, 120, 255), (255, 120, 0))  # first / second hand (handclean-pi)
WHITE = (255, 255, 255)

COLORS: dict[str, tuple[int, int, int]] = {"hand": (0, 200, 0), "dirt": (255, 0, 0)}


def render_frame(
    jpeg: bytes,
    crop: Crop,
    min_width: int,
    boxes: list[Box],
    quality: int,
    overlay: "Overlay | None" = None,
) -> bytes:
    """Camera JPEG -> display JPEG: scaled decode -> engine overlay -> crop [x1, y1, x2, y2] (0-1) -> draw boxes
    -> engine inset -> encode.

    Decodes at the smallest 1/n scale whose crop is still >= min_width wide, so the output
    is between min_width and 2x min_width (or the full crop if the camera is smaller).
    The overlay's levels and landmarks cover the whole camera frame, so they are drawn before the crop.
    """
    x1, y1, x2, y2 = crop
    img = simplejpeg.decode_jpeg(
        jpeg, min_width=math.ceil(min_width / (x2 - x1)), fastdct=True, fastupsample=True
    )
    if overlay is not None and overlay.live is not None:
        overlay.live.draw(img)
    if overlay is not None:
        for i, hand in enumerate(overlay.hands):
            draw_hand(img, hand, HAND_COLORS[i % len(HAND_COLORS)])
    h, w, _ = img.shape
    img = img[round(y1 * h) : round(y2 * h), round(x1 * w) : round(x2 * w)]
    draw_boxes(img, boxes)
    if overlay is not None and overlay.inset is not None:
        paste_inset(img, overlay.inset)
    return simplejpeg.encode_jpeg(np.ascontiguousarray(img), quality=quality)


def draw_boxes(img: Image, boxes: list[Box], thickness: int = 3) -> None:
    h, w, _ = img.shape
    for b in boxes:
        left, right = sorted(min(max(round(v * w), 0), w) for v in (b.x1, b.x2))
        top, bottom = sorted(min(max(round(v * h), 0), h) for v in (b.y1, b.y2))
        color = COLORS[b.cls]
        img[top : top + thickness, left:right] = color
        img[max(bottom - thickness, top) : bottom, left:right] = color
        img[top:bottom, left : left + thickness] = color
        img[top:bottom, max(right - thickness, left) : right] = color


# ---- HandCleanEngine overlay (display only; same look as handclean-pi/pi/handclean_pi.py) ----

CAPTURE_ALPHA = 0.6  # residue colour opacity on the last capture (handclean-pi EngineLoop._capture)
INSET_SHARE = 0.3  # inset width as a share of the display width. Set by us


class Overlay(NamedTuple):
    live: "LiveLevels | None"  # rough residue colours of the newest preview
    hands: tuple[Hand, ...]  # landmarks of the newest preview
    inset: Image | None  # last capture with its residue colours


def palette(colors: Sequence[str]) -> Palette:
    """'#rrggbb' colours of levels 1-5 -> 8 x 3 RGB lookup table indexed by level."""
    if len(colors) != 5:
        raise ValueError("HandCleanEngine needs 5 level colours")
    table = np.zeros((8, 3), np.uint8)
    for level, color in enumerate(colors, start=1):
        table[level] = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    return table


def resize_nearest(img: npt.NDArray[np.uint8], height: int, width: int) -> npt.NDArray[np.uint8]:
    rows = np.arange(height) * img.shape[0] // height
    cols = np.arange(width) * img.shape[1] // width
    return img[rows[:, None], cols]


def _residue_box(levels: LevelMap, height: int, width: int) -> tuple[int, int, LevelMap] | None:
    """(top, left, levels scaled to a height x width picture inside the box around levels 1-5); None = no residue."""
    ys, xs = np.nonzero((levels >= 1) & (levels <= 5))
    if ys.size == 0:
        return None
    lh, lw = levels.shape
    top, bottom = int(ys.min()) * height // lh, -(-(int(ys.max()) + 1) * height // lh)
    left, right = int(xs.min()) * width // lw, -(-(int(xs.max()) + 1) * width // lw)
    rows: npt.NDArray[np.intp] = np.arange(top, bottom, dtype=np.intp) * lh // height
    cols: npt.NDArray[np.intp] = np.arange(left, right, dtype=np.intp) * lw // width
    return top, left, levels[rows[:, None], cols]


def blend_levels(img: Image, levels: LevelMap, table: Palette, alpha: float) -> None:
    """In place: out = (1 - alpha) * pixel + alpha * colour[level] where 1 <= level <= 5.

    `levels` covers the whole of `img` at any size. For one-off pictures; the live picture uses LiveLevels.
    """
    box = _residue_box(levels, *img.shape[:2])
    if box is None:
        return
    top, left, box_levels = box
    mask = (box_levels >= 1) & (box_levels <= 5)
    region = img[top : top + box_levels.shape[0], left : left + box_levels.shape[1]]
    mixed = region[mask] * (1 - alpha) + table[box_levels[mask]] * alpha
    region[mask] = (mixed + 0.5).astype(np.uint8)


class LiveLevels:
    """Residue colours of one preview, drawn on every display frame until the next preview.

    out = pixel // 2 + colour[level] // 2 where 1 <= level <= 5 (alpha 0.5). The colours and mask are scaled to
    the display once, so a frame costs one shift, add and copy (laptop, residue over both hands: ~4 ms instead
    of ~145 ms). Only the Stream thread calls draw().
    """

    def __init__(self, levels: LevelMap, table: Palette) -> None:
        self._levels = levels
        self._half: Palette = (table >> 1).astype(np.uint8)
        self._size: tuple[int, int] | None = None
        self._scaled: tuple[int, int, npt.NDArray[np.bool_], Image] | None = None

    def draw(self, img: Image) -> None:
        height, width = img.shape[:2]
        if self._size != (height, width):
            self._size = (height, width)
            box = _residue_box(self._levels, height, width)
            if box is None:
                self._scaled = None
            else:
                top, left, box_levels = box
                mask = (box_levels >= 1) & (box_levels <= 5)
                self._scaled = (top, left, np.repeat(mask[..., None], 3, axis=2), self._half[box_levels])
        if self._scaled is None:
            return
        top, left, mask, half = self._scaled
        region = img[top : top + mask.shape[0], left : left + mask.shape[1]]
        np.copyto(region, (region >> 1) + half, where=mask)


def outline(img: Image, levels: LevelMap, color: tuple[int, int, int] = WHITE) -> None:
    """In place: 1 px outline of the hand (levels > 0); `levels` has the size of `img`."""
    hand = levels > 0
    inner = hand.copy()
    inner[1:] &= hand[:-1]
    inner[:-1] &= hand[1:]
    inner[:, 1:] &= hand[:, :-1]
    inner[:, :-1] &= hand[:, 1:]
    img[hand & ~inner] = color


def draw_hand(img: Image, hand: Hand, color: tuple[int, int, int], radius: int = 2) -> None:
    """In place: bones as lines of dots and joints as squares; points outside the image are clipped."""
    height, width = img.shape[:2]
    points: npt.NDArray[np.float64] = np.asarray(hand, np.float64) * np.array([width, height], np.float64)
    for a, b in BONES:
        start, end = points[a], points[b]
        steps = max(2, int(np.abs(end - start).max()) + 1)
        t = np.linspace(0.0, 1.0, steps)[:, None]
        line: npt.NDArray[np.intp] = np.rint(start + (end - start) * t).astype(np.intp)
        inside = (line[:, 0] >= 0) & (line[:, 0] < width) & (line[:, 1] >= 0) & (line[:, 1] < height)
        img[line[inside, 1], line[inside, 0]] = color
    for x, y in points.round().astype(np.intp):
        img[max(0, y - radius) : max(0, y + radius + 1), max(0, x - radius) : max(0, x + radius + 1)] = color


def capture_inset(frame: Image, levels: LevelMap, table: Palette) -> Image:
    """Picture of a capture with its residue colours and hand outline (handclean-pi EngineLoop._capture)."""
    inset = frame.copy()
    full_levels = resize_nearest(levels, *inset.shape[:2])
    blend_levels(inset, full_levels, table, CAPTURE_ALPHA)
    outline(inset, full_levels)
    return inset


def paste_inset(img: Image, inset: Image, margin: int = 8) -> None:
    """In place: `inset` scaled to INSET_SHARE of the width, in the bottom-right corner with a white border."""
    height, width = img.shape[:2]
    inset_width = min(width - 2 * margin - 2, round(width * INSET_SHARE))
    inset_height = min(height - 2 * margin - 2, round(inset_width * inset.shape[0] / inset.shape[1]))
    if inset_width <= 0 or inset_height <= 0:
        return
    top, left = height - margin - inset_height, width - margin - inset_width
    img[top - 1 : top + inset_height + 1, left - 1 : left + inset_width + 1] = WHITE
    img[top : top + inset_height, left : left + inset_width] = resize_nearest(inset, inset_height, inset_width)
