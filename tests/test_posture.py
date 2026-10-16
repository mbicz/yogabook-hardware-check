"""Angle/calibration regressions independent of device numbers and current pose."""
import math

import pytest

from checks.yogabook.posture import (UnitsUnavailable, angle_degrees, circular_distance,
                                    conversion_attributes, hinge_frame, hub_agreement,
                                    labeled_channels, planar_agreement)


@pytest.mark.parametrize(("raw", "scale", "offset", "expected"), [
    ("99", "0.017453293", "0", 99),
    ("104", "0.017453293", "-5", 99),
    ("198", "0.0087266465", "0", 99),
    (str(math.pi), "1", "0", 180),
    ("359", "0.017453293", "0", 359),
])
def test_raw_offset_scale_then_radian_to_degree_conversion(raw, scale, offset, expected):
    angle = angle_degrees(raw, scale, offset, "radians")
    assert angle["degrees"] == pytest.approx(expected, abs=0.0001)
    assert angle["radians"] == pytest.approx((float(raw) + float(offset)) * float(scale))
    assert angle["offset"] == float(offset)


@pytest.mark.parametrize(("scale", "offset", "unit"), [(None, "0", "radians"), ("1", None, "radians"),
                                                            ("1", "0", "unknown"), ("1", "0", "degrees")])
def test_missing_conversion_or_unestablished_units_are_unavailable(scale, offset, unit):
    with pytest.raises(UnitsUnavailable):
        angle_degrees("99", scale, offset, unit)


@pytest.mark.parametrize(("raw", "scale", "offset"), [
    ("nan", "1", "0"), ("inf", "1", "0"), ("1", "nan", "0"),
    ("1", "1", "inf"), ("1", "0", "0"), ("1", "-1", "0"),
    ("720", "0.017453293", "0"), ("-30", "0.017453293", "0"),
    ("1 2", "1", "0"), ("1e308", "1e308", "0"),
])
def test_nonfinite_invalid_scale_or_multiturn_values_do_not_wrap_to_a_pass(raw, scale, offset):
    with pytest.raises(ValueError):
        angle_degrees(raw, scale, offset, "radians")


def test_wrap_boundary_tolerates_exported_scale_rounding():
    assert circular_distance(angle_degrees("360", "0.017453293", "0", "radians")["degrees"], 0) < 0.001


def test_channel_labels_not_indices_establish_identity():
    assert labeled_channels({"in_angl7_label": "keyboard", "in_angl4_label": "hinge",
                             "in_angl9_label": "screen", "in_temp_label": "unrelated"}) == {
        "hinge": "in_angl4", "screen": "in_angl9", "keyboard": "in_angl7"}


@pytest.mark.parametrize("labels", [
    {"in_angl0_label": "hinge", "in_angl1_label": "screen"},
    {"in_angl0_label": "hinge", "in_angl1_label": "hinge", "in_angl2_label": "keyboard"},
    {"in_angl_label": "hinge", "in_angl1_label": "screen", "in_angl2_label": "keyboard"},
])
def test_missing_or_ambiguous_labeled_channels_fail(labels):
    with pytest.raises(ValueError):
        labeled_channels(labels)


def test_channel_specific_conversion_precedes_shared_type_conversion():
    assert conversion_attributes("in_angl9", {"in_angl9_scale", "in_angl_scale", "in_angl_offset"}) == (
        "in_angl9_scale", "in_angl_offset")
    with pytest.raises(UnitsUnavailable):
        conversion_attributes("in_angl9", {"in_angl_scale"})


def frame(hinge, screen, keyboard):
    channels = {"hinge": "in_angl4", "screen": "in_angl9", "keyboard": "in_angl7"}
    values = {"in_angl_scale": "0.017453293", "in_angl_offset": "0",
              "in_angl4_raw": str(hinge), "in_angl9_raw": str(screen), "in_angl7_raw": str(keyboard)}
    return hinge_frame(channels, values, "radians")


@pytest.mark.parametrize(("hinge", "screen", "keyboard", "expected_difference"), [
    (99, 101, 3, 1), (1, 357, 2, 4), (2, 181, 179, 0), (178, 181, 3, 0),
    (351, 354, 1, 2), (0, 354, 359, 5),
])
def test_documented_reference_poses_have_circular_not_exact_planar_agreement(hinge, screen, keyboard, expected_difference):
    agreement = planar_agreement(frame(hinge, screen, keyboard))
    assert agreement["difference_degrees"] == pytest.approx(expected_difference, abs=0.0001)
    assert 0 <= agreement["hinge_unsigned_degrees"] <= 180
    assert 0 <= agreement["projected_halves_degrees"] <= 180


@pytest.mark.parametrize(("first", "second", "expected"), [(359, 1, 2), (1, 359, 2), (0, 360, 0),
                                                           (180, 0, 180), (351, 1, 10)])
def test_circular_angles_do_not_use_linear_wrap_distance(first, second, expected):
    assert circular_distance(first, second) == expected


def test_two_hub_estimates_are_compared_not_forced_equal():
    differences = hub_agreement(frame(359, 358, 0), frame(1, 2, 3))
    assert differences == pytest.approx({"hinge": 2, "screen": 4, "keyboard": 3}, abs=0.0001)
    different_projection = planar_agreement(frame(100, 180, 0))
    assert different_projection["difference_degrees"] == pytest.approx(80, abs=0.0001)


def test_closed_and_folded_can_share_hinge_angle_without_sharing_pose():
    closed, folded = frame(2, 357, 2), frame(2, 181, 179)
    assert closed["hinge"] == folded["hinge"]
    assert hub_agreement(closed, folded)["screen"] > 170
    # No pose/lid classification is derivable from the identical hinge counts.
    assert closed["screen"]["degrees"] != folded["screen"]["degrees"]


@pytest.mark.parametrize("first", [float("nan"), float("inf")])
def test_nonfinite_circular_angles_are_rejected(first):
    with pytest.raises(ValueError):
        circular_distance(first, 0)
