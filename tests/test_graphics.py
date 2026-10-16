"""VA profile semantics and dynamic Intel DRM discovery, without a real GPU."""
import pytest

from checks.yogabook.diagnostics import intel_render_nodes, va_decode_missing, va_profiles


def test_vainfo_profiles_allow_ihd_chatter_and_successful_i965_profiles():
    content = """libva error: /usr/lib/dri/iHD_drv_video.so init failed
Driver version: Intel i965 driver for Intel(R) CherryView
 VAProfileH264High : VAEntrypointVLD
 VAProfileHEVCMain : VAEntrypointVLD
 VAProfileH264High : VAEntrypointEncSlice
 VAProfileH264High : VAEntrypointVLD
"""
    profiles = va_profiles(content)
    assert profiles == [("VAProfileH264High", "VAEntrypointEncSlice"),
                        ("VAProfileH264High", "VAEntrypointVLD"),
                        ("VAProfileHEVCMain", "VAEntrypointVLD")]
    assert va_decode_missing(profiles) == []


@pytest.mark.parametrize("content,missing", [
    ("", ["H.264 VLD decode", "HEVC Main VLD decode"]),
    ("VAProfileH264High : VAEntrypointEncSlice\nVAProfileHEVCMain : VAEntrypointEncSlice", ["H.264 VLD decode", "HEVC Main VLD decode"]),
    ("VAProfileH264ConstrainedBaseline : VAEntrypointVLD", ["HEVC Main VLD decode"]),
    ("VAProfileHEVCMain10 : VAEntrypointVLD", ["H.264 VLD decode", "HEVC Main VLD decode"]),
    ("VAProfileHEVCMain : VAEntrypointVLD", ["H.264 VLD decode"]),
    ("VAProfileH264High : VAEntrypointVLDExtra\nVAProfileHEVCMain : VAEntrypointVLD", ["H.264 VLD decode"]),
    ("libva error: vaInitialize failed; VAProfileH264High unavailable", ["H.264 VLD decode", "HEVC Main VLD decode"]),
])
def test_profile_names_entrypoints_and_errors_are_not_decode_proof(content, missing):
    assert va_decode_missing(va_profiles(content)) == missing


def test_profile_count_does_not_define_success():
    content = "VAProfileH264Main : VAEntrypointVLD\nVAProfileHEVCMain : VAEntrypointVLD"
    assert len(va_profiles(content)) == 2
    assert va_decode_missing(va_profiles(content)) == []


def render_node(root, name, vendor="0x8086", device_id="0x22b0", driver="i915"):
    node = root / name
    device = node / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor)
    (device / "device").write_text(device_id)
    if driver:
        target = root / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (device / "driver").symlink_to(target, target_is_directory=True)
    return node


def test_render_discovery_is_dynamic_and_ignores_other_vendors(tmp_path):
    render_node(tmp_path, "renderD173")
    render_node(tmp_path, "renderD128", vendor="0x1002", device_id="0x1234", driver="amdgpu")
    render_node(tmp_path, "card0")
    found = intel_render_nodes(tmp_path)
    assert [item["name"] for item in found] == ["renderD173"]
    assert found[0]["device_id"] == "0x22b0"
    assert found[0]["driver"] == "i915"


def test_unbound_intel_gpu_stays_visible_as_unbound(tmp_path):
    render_node(tmp_path, "renderD211", driver=None)
    found = intel_render_nodes(tmp_path)
    assert len(found) == 1
    assert found[0]["driver"] is None


def test_incomplete_render_node_does_not_invent_intel_identity(tmp_path):
    (tmp_path / "renderD132/device").mkdir(parents=True)
    assert intel_render_nodes(tmp_path) == []


@pytest.mark.parametrize("initial_code,fallback_code,should_fail,expected_calls", [
    (0, 0, False, 1),
    (1, 0, False, 2),
    (1, 1, True, 2),
])
def test_va_query_requires_successful_process_and_real_fallback(monkeypatch, initial_code, fallback_code, should_fail, expected_calls):
    from types import SimpleNamespace
    from checks.yogabook import test_graphics as board

    monkeypatch.setattr(board, "render_nodes", lambda evidence: [{"name": "renderD197"}])
    profiles = "VAProfileH264High : VAEntrypointVLD\nVAProfileHEVCMain : VAEntrypointVLD\n"
    calls = []
    def shell(*argv, **kwargs):
        calls.append(argv)
        code = initial_code if len(calls) == 1 else fallback_code
        return SimpleNamespace(returncode=code, stdout=profiles, stderr="iHD init failed" if code else "")
    observations = {}
    if should_fail:
        with pytest.raises(AssertionError, match="initialization failed"):
            board.test_vaapi_decode(shell, observations.__setitem__, lambda name: name)
    else:
        board.test_vaapi_decode(shell, observations.__setitem__, lambda name: name)
    assert len(calls) == expected_calls
    assert "--display" in calls[0] and "drm" in calls[0]
    assert "/dev/dri/renderD197" in calls[0]
    if expected_calls == 2:
        assert calls[1][:2] == ("env", "LIBVA_DRIVER_NAME=i965")
        assert len(observations["vaapi_queries"]) == 2
