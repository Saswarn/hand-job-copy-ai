import numpy as np
import pytest
import simplejpeg

from app.utils.image import (
    COLORS, HAND_COLORS, WHITE, Box, LiveLevels, Overlay, blend_levels, capture_inset, draw_boxes, draw_hand, outline,
    palette, paste_inset, render_frame,
)

TABLE = palette(["#00e5ff", "#00e65a", "#ffeb00", "#ff8c00", "#ff0040"])


def test_render_frame_crops_and_draws_box() -> None:
    src = simplejpeg.encode_jpeg(np.full((100, 200, 3), 128, np.uint8), quality=95)
    box = [Box("dirt", 0.9, 0, 0, 1, 1)]

    img = simplejpeg.decode_jpeg(render_frame(src, (0, 0, 0.5, 1), 100, box, 95))
    assert img.shape[:2] == (100, 100)  # left half at full scale: 100 / 0.5 needs the whole 200 px
    r, g, _ = img[50, 1]
    assert r > 200 and g < 80  # red dirt box on the left edge
    assert abs(int(img[50, 50, 0]) - 128) < 10  # inside untouched

    img = simplejpeg.decode_jpeg(render_frame(src, (0, 0, 0.5, 1), 50, box, 95))
    assert img.shape[:2] == (50, 50)  # 1/2-scale decode is already wide enough


def test_draw_boxes_clamps_and_accepts_swapped_corners() -> None:
    img = np.zeros((10, 10, 3), np.uint8)
    draw_boxes(img, [Box("hand", 1.0, 1.5, 1.2, 0.5, 0.5)], thickness=1)  # x1 > x2, past the edge
    assert tuple(img[5, 5]) == COLORS["hand"]  # top-left corner at (0.5, 0.5)
    assert tuple(img[9, 9]) == COLORS["hand"]  # clamped to the last pixel
    assert tuple(img[0, 0]) == (0, 0, 0)  # outside the box untouched


def test_palette_maps_levels_1_to_5_and_rejects_wrong_count() -> None:
    assert TABLE[1].tolist() == [0x00, 0xE5, 0xFF] and TABLE[5].tolist() == [0xFF, 0x00, 0x40]
    assert TABLE[0].tolist() == TABLE[6].tolist() == [0, 0, 0]
    with pytest.raises(ValueError, match="5 level colours"):
        palette(["#000000"])


def test_blend_levels_colours_only_residue_levels() -> None:
    img = np.full((40, 80, 3), 100, np.uint8)
    levels = np.zeros((4, 8), np.uint8)  # one tenth of the picture per cell
    levels[1, 2] = 5  # residue
    levels[2, 2] = 6  # clean skin: not coloured
    levels[3, 7] = 7  # unusable: not coloured
    blend_levels(img, levels, TABLE, 0.5)
    expected = (np.array([100, 100, 100]) * 0.5 + TABLE[5] * 0.5 + 0.5).astype(np.uint8)
    assert (img[10:20, 20:30] == expected).all()
    untouched = np.ones((40, 80), bool)
    untouched[10:20, 20:30] = False
    assert (img[untouched] == 100).all()


def test_blend_levels_without_residue_is_a_no_op() -> None:
    img = np.full((10, 10, 3), 7, np.uint8)
    blend_levels(img, np.full((5, 5), 6, np.uint8), TABLE, 0.5)
    assert (img == 7).all()


def test_outline_marks_the_hand_edge_only() -> None:
    img = np.zeros((7, 7, 3), np.uint8)
    levels = np.zeros((7, 7), np.uint8)
    levels[1:6, 1:6] = 6
    outline(img, levels)
    assert tuple(img[1, 3]) == WHITE and tuple(img[3, 1]) == WHITE
    assert tuple(img[3, 3]) == (0, 0, 0) and tuple(img[0, 0]) == (0, 0, 0)


def test_draw_hand_clips_points_outside_the_image() -> None:
    img = np.zeros((20, 20, 3), np.uint8)
    hand = [(0.5, 0.5)] * 20 + [(1.5, -0.5)]  # last landmark off the picture
    draw_hand(img, hand, HAND_COLORS[0])
    assert tuple(img[10, 10]) == HAND_COLORS[0]
    assert tuple(img[19, 0]) == (0, 0, 0)


def test_capture_inset_scales_the_level_map_to_the_frame() -> None:
    frame = np.full((40, 80, 3), 50, np.uint8)
    levels = np.full((4, 8), 6, np.uint8)
    levels[0, 0] = 1
    levels[3] = 0  # bottom quarter is not hand
    inset = capture_inset(frame, levels, TABLE)
    assert inset.shape == frame.shape and (frame == 50).all()  # original untouched
    assert tuple(inset[5, 5]) != (50, 50, 50) and tuple(inset[20, 40]) == (50, 50, 50)
    assert tuple(inset[29, 40]) == WHITE  # hand outline at the edge
    assert tuple(inset[35, 40]) == (50, 50, 50)


def test_paste_inset_stays_inside_the_picture() -> None:
    img = np.zeros((100, 200, 3), np.uint8)
    paste_inset(img, np.full((30, 40, 3), 9, np.uint8))
    assert tuple(img[-1, -1]) == (0, 0, 0)  # margin kept
    assert tuple(img[100 - 8 - 1, 200 - 8 - 1]) == (9, 9, 9)
    assert tuple(img[100 - 8, 200 - 8]) == WHITE  # border
    assert tuple(img[10, 10]) == (0, 0, 0)


def test_render_frame_draws_overlay_before_crop() -> None:
    src = simplejpeg.encode_jpeg(np.full((100, 200, 3), 128, np.uint8), quality=95)
    levels = np.zeros((10, 20), np.uint8)
    levels[:, 10:] = 5  # right half of the camera frame
    overlay = Overlay(LiveLevels(levels, TABLE), (), None)
    img = simplejpeg.decode_jpeg(render_frame(src, (0.5, 0, 1, 1), 100, [], 95, overlay))
    assert img.shape[:2] == (100, 100)
    r, g, b = (int(v) for v in img[50, 10])  # left of the crop = right half of the camera frame
    assert r > 170 and g < 110 and b < 140  # mixed with #ff0040


def test_live_levels_mix_half_and_rescale_for_a_new_display_size() -> None:
    levels = np.zeros((4, 8), np.uint8)
    levels[1, 2] = 5
    levels[2, 2] = 6  # clean skin: not coloured
    live = LiveLevels(levels, TABLE)
    expected = np.array([100, 100, 100]) // 2 + TABLE[5] // 2
    for height, width in [(40, 80), (20, 40), (40, 80)]:  # the scaled layer follows the display size
        img = np.full((height, width, 3), 100, np.uint8)
        live.draw(img)
        cell_h, cell_w = height // 4, width // 8
        residue = img[cell_h : 2 * cell_h, 2 * cell_w : 3 * cell_w]
        assert (residue == expected).all()
        untouched = np.ones((height, width), bool)
        untouched[cell_h : 2 * cell_h, 2 * cell_w : 3 * cell_w] = False
        assert (img[untouched] == 100).all()


def test_live_levels_without_residue_draw_nothing() -> None:
    img = np.full((10, 10, 3), 7, np.uint8)
    LiveLevels(np.full((5, 5), 6, np.uint8), TABLE).draw(img)
    assert (img == 7).all()
