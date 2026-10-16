"""Read-only Intel DRM/VA capability queries; no benchmark or display changes."""
import json
from pathlib import Path
import sys

import pytest

from checks.yogabook.diagnostics import intel_render_nodes, va_decode_missing, va_profiles


def render_nodes(evidence):
    try:
        found = intel_render_nodes(Path("/sys/class/drm"))
    except PermissionError as exc:
        pytest.skip(f"blocked: cannot inspect DRM sysfs: {exc}")
    evidence("intel_render_nodes", found)
    assert found, "required Intel GPU render node missing"
    assert all(item["driver"] == "i915" for item in found), f"Intel render node is not bound to i915: {found}"
    for item in found:
        assert (Path("/dev/dri") / item["name"]).exists(), f"missing render device for {item}"
    return found


@pytest.mark.hardware(id="graphics.render_node.ioctl", component="graphics", layer="headless")
def test_render_ioctl(shell, evidence):
    queries = []
    for item in render_nodes(evidence):
        node = str(Path("/dev/dri") / item["name"])
        result = shell(sys.executable, "-c", '''import json, sys
sys.path.insert(0, sys.argv[2])
from checks.yogabook.diagnostics import i915_query
try:
    print(json.dumps(i915_query(sys.argv[1])))
except PermissionError as exc:
    print(str(exc)); sys.exit(78)
''', node, str(Path(__file__).resolve().parents[2]), timeout=5)
        if result.returncode == 78:
            pytest.skip(f"blocked: render node permissions: {result.stdout.strip()}")
        assert result.returncode == 0, f"DRM/i915 read query failed: {result.stderr}"
        data = json.loads(result.stdout)
        queries.append({"node": node, **data})
        evidence("drm_queries", queries)
        assert data["driver"] == "i915", data
        assert data["chipset_id"] == int(item["device_id"], 16), f"i915 ioctl/sysfs device identity mismatch: {data}, {item}"
    evidence("limitations", "DRM_IOCTL_VERSION and I915_GETPARAM CHIPSET_ID readback only; not 3D command execution, compositor acceleration, visible rendering or a load benchmark. No DMC firmware is required on Cherryview.")


@pytest.mark.hardware(id="graphics.vaapi.decode_profiles", component="graphics.video", layer="headless")
def test_vaapi_decode(shell, evidence, require_program):
    vainfo = require_program("vainfo")
    attempts = []
    for item in render_nodes(evidence):
        node = str(Path("/dev/dri") / item["name"])
        args = (vainfo, "--display", "drm", "--device", node)
        initial = shell(*args, timeout=12)
        profiles = va_profiles(initial.stdout + "\n" + initial.stderr)
        attempts.append({"node": node, "driver_selection": "libva default", "returncode": initial.returncode,
                         "profiles": profiles, "stdout": initial.stdout, "stderr": initial.stderr})
        result = initial
        # iHD cannot initialize CHV. A real successful i965 query can establish
        # recovery; suppressing iHD's error without that query cannot.
        if initial.returncode or va_decode_missing(profiles):
            fallback = shell("env", "LIBVA_DRIVER_NAME=i965", *args, timeout=12)
            fallback_profiles = va_profiles(fallback.stdout + "\n" + fallback.stderr)
            attempts.append({"node": node, "driver_selection": "explicit i965 fallback", "returncode": fallback.returncode,
                             "profiles": fallback_profiles, "stdout": fallback.stdout, "stderr": fallback.stderr})
            result, profiles = fallback, fallback_profiles
        evidence("vaapi_queries", attempts)
        if result.returncode and "permission denied" in (result.stdout + result.stderr).lower():
            pytest.skip(f"blocked: VA-API render-node permissions on {node}")
        assert result.returncode == 0, f"VA-API initialization failed on {node}; see default and i965 query evidence"
        assert profiles, f"vainfo succeeded without parseable profile/entrypoint pairs on {node}"
        missing = va_decode_missing(profiles)
        assert not missing, f"CHV decode capability missing on {node}: {missing}; actual profiles: {profiles}"
    evidence("limitations", "Actual headless DRM libva initialization and advertised H.264/HEVC Main VLD pairs only, not a decoded frame, throughput or image quality. No fixed profile count; encoding-only entries cannot satisfy decode. Successful real i965 fallback can recover expected iHD initialization failure.")
