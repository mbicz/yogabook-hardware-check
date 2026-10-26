"""Enumeration is safe; image acquisition and microphone/speaker I/O need opt-in."""
from pathlib import Path
import re
import subprocess

import pytest

from checks.yogabook.attachments import firmware_files
from checks.yogabook.media import (
    blocked_audio_error, camera_inventory, observe_pcm, owned_process,
    parse_alsa_cards, parse_camera_result, parse_capture_parameters, parse_ucm_names,
    parse_ucm_pcm,
    sustained_pcm_progress, write_tone,
)


def _physical_card(evidence):
    cards_file = Path("/proc/asound/cards")
    assert cards_file.exists(), "ALSA card registry missing"
    cards = parse_alsa_cards(cards_file.read_text())
    evidence("alsa_cards", cards)
    assert "chtyogabook" in cards, "required physical chtyogabook card absent (desktop Dummy Output is not this check)"
    return cards["chtyogabook"]


def _audio_result(result, operation):
    if result.returncode and blocked_audio_error(result.stderr + result.stdout):
        pytest.skip(f"blocked: {operation}: {result.stderr.strip() or result.stdout.strip()}")
    assert result.returncode == 0, f"{operation}: {result.stderr}\n{result.stdout}"


def _ucm(shell, alsaucm, commands):
    result = shell(alsaucm, "-c", "hw:chtyogabook", "-b", "-", input=commands, timeout=10)
    _audio_result(result, "UCM configuration")
    return result.stdout


@pytest.mark.parametrize("sensor", [
    pytest.param("ov2740", marks=pytest.mark.hardware(id="camera.front.enumeration", component="camera.front", layer="enumeration")),
    pytest.param("ov8858", marks=pytest.mark.hardware(id="camera.rear.enumeration", component="camera.rear", layer="enumeration")),
])
def test_camera_binding(sensor, evidence):
    inventory = camera_inventory()
    evidence("video_devices", inventory)
    sensors = [item for item in inventory if sensor in item["name"].lower()]
    assert sensors, f"required {sensor} V4L2 sensor absent"
    assert any(item["driver"] == sensor for item in sensors), f"{sensor} not bound to its sensor driver"
    assert any("atomisp" in item["name"].lower() and re.fullmatch(r"/dev/video\d+", item["node"])
               and (item["driver"] or "").startswith("atomisp") for item in inventory), "AtomISP capture binding absent"


@pytest.mark.active("camera")
@pytest.mark.parametrize("sensor", [
    pytest.param("ov2740", marks=pytest.mark.hardware(id="camera.front.stream", component="camera.front", layer="headless")),
    pytest.param("ov8858", marks=pytest.mark.hardware(id="camera.rear.stream", component="camera.rear", layer="headless")),
])
def test_camera_mmap_stream(sensor, tmp_path, shell, require_program, evidence):
    cc = require_program("cc")
    source = Path(__file__).with_name("native") / "camera_smoke.c"
    if not source.is_file():
        pytest.fail("board native/camera_smoke.c is missing")
    source_path = tmp_path / "camera_smoke.c"
    source_path.write_bytes(source.read_bytes())
    executable = tmp_path / "camera_smoke"
    build = shell(cc, "-std=c11", "-O2", "-Wall", "-Wextra", str(source_path), "-o", str(executable), timeout=30)
    if build.returncode and re.search(r"fatal error:.*(?:No such file or directory|file not found)", build.stderr):
        pytest.skip(f"blocked: native compiler development headers unavailable: {build.stderr.strip()}")
    assert build.returncode == 0, f"native camera helper compilation failed: {build.stderr}"
    result = shell(str(executable), sensor, timeout=32)
    evidence("camera_stderr", result.stderr)
    if result.returncode == 78:
        pytest.skip(f"blocked: camera device busy or access denied: {result.stderr.strip()}")
    assert result.returncode == 0, f"V4L2 streaming failed ({result.returncode}): {result.stderr}"
    report = parse_camera_result(result.stdout, sensor)
    evidence("camera_stream", report)
    evidence("limits", "40 MMAP frames; no scene brightness, color correctness, or desktop camera acceptance claimed; no frames retained")


@pytest.mark.hardware(id="audio.physical.enumeration", component="audio", layer="enumeration")
def test_audio_card_codec_firmware(evidence):
    card = _physical_card(evidence)
    evidence("physical_card", {"id": "chtyogabook", "index": card})
    codec_devices = []
    for device in Path("/sys/bus/i2c/devices").glob("*"):
        name = device / "name"
        if name.is_file() and "rt5677" in name.read_text().lower():
            driver = device / "driver"
            codec_devices.append({"path": str(device), "driver": driver.resolve().name if driver.exists() else None})
    evidence("codec_devices", codec_devices)
    assert codec_devices, "required RT5677 codec absent"
    assert any(item["driver"] and "rt5677" in item["driver"] for item in codec_devices), "RT5677 codec driver unbound"
    firmware = firmware_files(("intel/fw_sst_22a8.bin",))
    evidence("sst_firmware_files", sorted(set(firmware)))
    assert firmware, "required SST intel/fw_sst_22a8.bin firmware absent"
    evidence("limits", "physical ALSA/codec enumeration and installed firmware, not audible output or PipeWire-session health")


@pytest.mark.hardware(id="audio.ucm.enumeration", component="audio", layer="enumeration")
def test_audio_ucm(shell, require_program, evidence):
    _physical_card(evidence)
    alsaucm = require_program("alsaucm")
    result = shell(alsaucm, "-c", "hw:chtyogabook", "list", "_verbs", timeout=10)
    _audio_result(result, "UCM verb enumeration")
    verbs = parse_ucm_names(result.stdout)
    evidence("ucm_verbs", verbs)
    assert "HiFi" in verbs, f"physical card has no HiFi UCM verb: {result.stdout}"
    # Read installed configuration rather than applying EnableSequence in a safe check.
    configs = []
    for root in (Path("/usr/share/alsa/ucm2"), Path("/etc/alsa/ucm2")):
        for profile in root.glob("**/cht-yogabook/*.conf"):
            text = profile.read_text(errors="replace")
            configs.append({"path": str(profile), "speaker1": bool(re.search(r'SectionDevice\.\s*["\']?Speaker1\b', text))})
    evidence("ucm_profile_files", configs)
    assert any(item["speaker1"] for item in configs), "cht-yogabook Speaker1 UCM device configuration absent"


def _save_mixer(shell, alsactl, state, card):
    result = shell(alsactl, "-f", str(state), "store", str(card), timeout=10)
    _audio_result(result, "saving ALSA mixer")
    assert state.is_file(), "alsactl did not create restoration state"


def _restore_mixer(shell, alsactl, state, card):
    result = shell(alsactl, "-f", str(state), "restore", str(card), timeout=10)
    # Restoration failures are failures even if exercise was blocked/skipped.
    assert result.returncode == 0, f"ALSA mixer restoration failed: {result.stderr}"


@pytest.mark.active("audio")
@pytest.mark.hardware(id="audio.speaker.pcm", component="audio.speaker", layer="headless")
def test_audio_sustained_playback(tmp_path, shell, require_program, require_root, evidence):
    require_root()
    card = _physical_card(evidence)
    alsactl, alsaucm, aplay = [require_program(name) for name in ("alsactl", "alsaucm", "aplay")]
    state, tone, diagnostic = (tmp_path / name for name in ("mixer.state", "tone.wav", "aplay.log"))
    write_tone(tone)
    _save_mixer(shell, alsactl, state, card)
    try:
        _ucm(shell, alsaucm, "set _verb HiFi\nset _enadev Speaker1\n")
        with owned_process([aplay, "-q", "-D", "hw:CARD=chtyogabook,DEV=1", str(tone)], diagnostic) as process:
            samples = observe_pcm(process, Path(f"/proc/asound/card{card}/pcm1p/sub0/status"))
            evidence("playback_status", samples)
            if not sustained_pcm_progress(samples):
                text = diagnostic.read_text()
                if blocked_audio_error(text):
                    pytest.skip(f"blocked: aplay: {text.strip()}")
                pytest.fail(f"physical playback not RUNNING with advancing hw_ptr: {samples}; {text}")
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pytest.fail("bounded eight-second aplay tone failed to finish")
            assert code == 0, diagnostic.read_text()
        evidence("playback", {"pcm": "hw:CARD=chtyogabook,DEV=1", "rate": 48000, "channels": 2,
                              "format": "S16_LE", "duration_seconds": 8, "tone_dbfs": -36})
        evidence("limits", "PCM transport/routing exercise only; no human audibility claim")
    finally:
        _restore_mixer(shell, alsactl, state, card)


@pytest.mark.active("audio")
@pytest.mark.hardware(id="audio.microphone.pcm", component="audio.microphone", layer="headless")
def test_audio_microphone_capture(tmp_path, shell, require_program, require_root, evidence):
    require_root()
    card = _physical_card(evidence)
    alsactl, alsaucm, arecord = [require_program(name) for name in ("alsactl", "alsaucm", "arecord")]
    state, diagnostic = tmp_path / "mixer.state", tmp_path / "arecord.log"
    _save_mixer(shell, alsactl, state, card)
    try:
        devices = parse_ucm_names(_ucm(shell, alsaucm, "set _verb HiFi\nlist _devices\n"))
        evidence("ucm_devices", devices)
        microphones = [name for name in devices if name.lower() in ("mic1", "internalmic", "internal mic", "dmic")]
        if len(microphones) != 1:
            pytest.skip(f"blocked: no unambiguous built-in microphone UCM device: {devices}")
        microphone = microphones[0]
        output = _ucm(shell, alsaucm, f"set _verb HiFi\nset _enadev {microphone}\nget CapturePCM/{microphone}\n")
        matches = parse_ucm_pcm(output, "chtyogabook")
        if len(matches) != 1:
            pytest.skip(f"blocked: cannot resolve physical UCM CapturePCM: {output.strip()}")
        pcm = matches[0]
        device_match = re.search(r",(?:DEV=)?(\d+)$", pcm)
        device = int(device_match[1]) if device_match else 0  # ALSA's documented omitted DEV default.
        query = shell(arecord, "-D", pcm, "--dump-hw-params", "-t", "raw", "-d", "1", "/dev/null", timeout=6)
        capabilities = query.stdout + query.stderr
        evidence("capture_capabilities", capabilities)
        if blocked_audio_error(capabilities):
            pytest.skip(f"blocked: microphone PCM access: {capabilities.strip()}")
        if query.returncode and "FORMAT:" not in capabilities:
            pytest.fail(f"microphone capability query failed ({query.returncode}): {capabilities}")
        try:
            parameters = parse_capture_parameters(capabilities)
        except ValueError as error:
            pytest.skip(f"blocked: cannot choose advertised microphone parameters: {error}")
        argv = [arecord, "-q", "-D", pcm, "-t", "raw", "-f", parameters.format,
                "-r", str(parameters.rate), "-c", str(parameters.channels), "-d", "4", "/dev/null"]
        with owned_process(argv, diagnostic) as process:
            samples = observe_pcm(process, Path(f"/proc/asound/card{card}/pcm{device}c/sub0/status"))
            evidence("capture_status", samples)
            if not sustained_pcm_progress(samples):
                text = diagnostic.read_text()
                if blocked_audio_error(text):
                    pytest.skip(f"blocked: microphone PCM access: {text.strip()}")
                pytest.fail(f"microphone capture not RUNNING with advancing hw_ptr: {samples}; {text}")
            try:
                code = process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                pytest.fail("bounded four-second arecord failed to finish")
            assert code == 0, diagnostic.read_text()
        evidence("capture", {"pcm": pcm, "format": parameters.format, "rate": parameters.rate,
                             "channels": parameters.channels, "duration_seconds": 4, "destination": "/dev/null"})
        evidence("limits", "capture transport only, no acoustic quality claim; microphone samples discarded")
    finally:
        _restore_mixer(shell, alsactl, state, card)
