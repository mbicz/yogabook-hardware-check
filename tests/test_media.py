import json
import struct
import wave

import pytest

from checks.yogabook.media import (
    camera_inventory, parse_alsa_cards, parse_camera_result,
    parse_capture_parameters, parse_pcm_status, parse_ucm_names,
    running_pointer_advanced, sustained_pcm_progress, write_tone,
)


def camera_report(sensor="ov2740"):
    geometry = {"width": 400, "height": 300, "stride": 832, "bytes": 250000}
    return dict(geometry, frames=40, buffers=8, sensor=sensor,
                node="/dev/video19", inputs=["ov8858 2-0036", "ov2740 2-0010"],
                minimum_bytesused=geometry["stride"] * geometry["height"],
                maximum_bytesused=geometry["bytes"])


@pytest.mark.parametrize("sensor", ["ov2740", "ov8858"])
def test_capture_accepts_negotiated_padding_not_just_visible_pixels(sensor):
    # AtomISP's sizeimage includes tail padding beyond stride * height.
    report = camera_report(sensor)
    assert parse_camera_result(json.dumps(report), sensor)["maximum_bytesused"] == report["bytes"]


@pytest.mark.parametrize("change", [
    {"frames": 39}, {"buffers": 4}, {"stride": 798}, {"bytes": 249599},
    {"minimum_bytesused": 400 * 300 * 2}, {"maximum_bytesused": 250001},
    {"minimum_bytesused": 250000, "maximum_bytesused": 249600},
    {"frames": True}, {"sensor": "ov8858"}, {"inputs": ["ov8858"]},
    {"node": "/dev/v4l-subdev2"}, {"width": 400.0}, {"width": 0},
    {"height": -1}, {"stride": True}, {"bytes": 0},
])
def test_capture_rejects_short_error_wrong_sensor_and_out_of_bounds_reports(change):
    report = camera_report()
    report.update(change)
    with pytest.raises(ValueError):
        parse_camera_result(json.dumps(report), "ov2740")


def test_capture_accepts_safe_changed_dimensions_stride_and_tail_padding():
    report = camera_report()
    report.update(width=640, height=480, stride=1344, bytes=645632,
                  minimum_bytesused=645120, maximum_bytesused=645632)
    result = parse_camera_result(json.dumps(report), "ov2740")
    assert (result["width"], result["height"], result["stride"], result["bytes"]) == (
        640, 480, 1344, 645632)


def test_camera_discovery_uses_names_and_driver_not_indices(tmp_path):
    for name, identity, driver in (("video19", "ATOMISP video output", "atomisp-isp2"),
                                   ("v4l-subdev12", "ov2740 2-0010", "ov2740")):
        node = tmp_path / name
        (node / "device").mkdir(parents=True)
        (node / "name").write_text(identity + "\n")
        driver_path = tmp_path / "drivers" / driver
        driver_path.mkdir(parents=True, exist_ok=True)
        (node / "device/driver").symlink_to(driver_path, target_is_directory=True)
    nodes = camera_inventory(tmp_path)
    assert {item["node"]: item["driver"] for item in nodes} == {
        "/dev/video19": "atomisp-isp2", "/dev/v4l-subdev12": "ov2740"}


def test_cards_parse_stable_identity_when_indices_change():
    cards = parse_alsa_cards(" 0 [HDMI           ]: HDA-Intel - HDMI\n"
                             " 7 [chtyogabook    ]: cht-yogabook - cht-yogabook\n"
                             "                      cht-yogabook continuation\n")
    assert cards["chtyogabook"] == 7
    assert cards["HDMI"] == 0


def test_duplicate_alsa_card_identity_is_not_silently_overwritten():
    with pytest.raises(ValueError, match="ambiguous"):
        parse_alsa_cards(" 0 [chtyogabook]: one\n 2 [chtyogabook]: two\n")


def test_pcm_hardware_pointer_must_advance_while_running():
    samples = [parse_pcm_status("state: RUNNING\nhw_ptr: 3840\nappl_ptr: 8192\n"),
               parse_pcm_status("state: RUNNING\nhw_ptr: 7680\nappl_ptr: 12288\n")]
    assert running_pointer_advanced(samples)


@pytest.mark.parametrize("samples", [
    [{"state": "RUNNING", "hw_ptr": 7}, {"state": "DRAINING", "hw_ptr": 999}],
    [{"state": "RUNNING", "hw_ptr": 7, "appl_ptr": 8},
     {"state": "RUNNING", "hw_ptr": 7, "appl_ptr": 999}],
    [{"state": "RUNNING", "hw_ptr": 999}, {"state": "RUNNING", "hw_ptr": 7}],
    [{"state": "OPEN"}, {"state": "RUNNING", "hw_ptr": 7}],
])
def test_draining_application_only_progress_and_pointer_reset_do_not_prove_running(samples):
    assert not running_pointer_advanced(samples)


def test_pcm_requires_sustained_running_not_a_short_successful_burst():
    burst = [{"state": "RUNNING", "hw_ptr": 0, "elapsed_seconds": 0.0},
             {"state": "RUNNING", "hw_ptr": 4800, "elapsed_seconds": 0.1}]
    assert not sustained_pcm_progress(burst)
    assert sustained_pcm_progress(burst + [
        {"state": "RUNNING", "hw_ptr": 105600, "elapsed_seconds": 2.2}])
    assert not sustained_pcm_progress(burst + [
        {"state": "XRUN", "elapsed_seconds": 1.0},
        {"state": "RUNNING", "hw_ptr": 105600, "elapsed_seconds": 2.2}])


def test_closed_pcm_status_cannot_be_misread_as_running():
    with pytest.raises(ValueError, match="no state"):
        parse_pcm_status("closed\n")
    with pytest.raises(ValueError, match="invalid PCM hw_ptr"):
        parse_pcm_status("state: RUNNING\nhw_ptr: garbage\n")


def test_capture_parameters_selected_from_advertised_intervals():
    value = parse_capture_parameters("FORMAT: S16_LE S32_LE\nCHANNELS: [1 2]\nRATE: [8000 48000]\n")
    assert (value.format, value.rate, value.channels, value.sample_bytes) == ("S16_LE", 48000, 2, 2)


def test_capture_parameters_respect_fixed_nonstandard_parameters():
    value = parse_capture_parameters("FORMAT: S32_LE\nCHANNELS: 1\nRATE: 16000\n")
    assert (value.format, value.rate, value.channels) == ("S32_LE", 16000, 1)


@pytest.mark.parametrize("capabilities", [
    "FORMAT: FLOAT_LE\nCHANNELS: 2\nRATE: 48000\n",
    "FORMAT: S16_LE\nCHANNELS: [4 6]\nRATE: [96000 192000]\n",
    "FORMAT: S16_LE\nCHANNELS: 2\nRATE: (8000 48000)\n",
    "arecord: main: audio open error: Input/output error\n",
])
def test_capture_parameters_do_not_guess_unknown_or_unsupported_constraints(capabilities):
    with pytest.raises(ValueError):
        parse_capture_parameters(capabilities)


def test_ucm_list_does_not_treat_descriptions_as_device_names():
    assert parse_ucm_names('  0: HiFi\n    High fidelity\n1: "Speaker1"\n   Internal speakers\n') == ["HiFi", "Speaker1"]


def test_low_amplitude_tone_is_real_stereo_pcm_with_expected_duration(tmp_path):
    destination = tmp_path / "tone.wav"
    write_tone(destination, seconds=1)
    with wave.open(str(destination), "rb") as tone:
        assert (tone.getnchannels(), tone.getframerate(), tone.getsampwidth(), tone.getnframes()) == (2, 48000, 2, 48000)
        frames = tone.readframes(tone.getnframes())
    samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    assert samples[::2] == samples[1::2]
    assert 400 < max(samples) < 600
    assert -600 < min(samples) < -400
    assert len(set(samples)) > 100
