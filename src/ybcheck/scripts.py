"""Collect complete shell programs, not an assertion language."""

import json
import shlex
from pathlib import Path

import pytest

from .commands import run_command


class ScriptError(RuntimeError):
    pass


def script_metadata(path):
    lines = Path(path).read_text().splitlines()
    headers = [line.partition("# ybcheck:")[2].strip() for line in lines[:25]
               if line.startswith("# ybcheck:")]
    if len(headers) > 1:
        raise ValueError(f"{path}: only one ybcheck metadata header is allowed")
    metadata = json.loads(headers[0]) if headers else {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: metadata must be a JSON object")
    timeout = metadata.get("timeout", 10)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise ValueError(f"{path}: timeout must be between 0 and 600 seconds")
    return metadata


def script_command(path):
    first = Path(path).read_text().splitlines()[0:1]
    interpreter = shlex.split(first[0][2:]) if first and first[0].startswith("#!") else ["/bin/sh"]
    if not interpreter:
        raise ValueError(f"{path}: empty interpreter")
    return [*interpreter, str(Path(path).resolve())]


def assert_script_result(result):
    detail = f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    if result.returncode == 0:
        return
    if result.returncode == 1:
        pytest.fail(detail, pytrace=False)
    if result.returncode == 77:
        pytest.skip(f"skip: {detail}")
    if result.returncode == 78:
        pytest.skip(f"blocked: {detail}")
    if result.returncode == 79:
        import warnings
        from .plugin import HardwareWarning
        warnings.warn(detail, HardwareWarning, stacklevel=2)
        return
    raise ScriptError(detail)


class ShellFile(pytest.File):
    def collect(self):
        metadata = script_metadata(self.path)
        item = ShellItem.from_parent(self, name=self.path.name)
        default_id = self.nodeid.removesuffix(".sh").replace("/", ".")
        item.add_marker(pytest.mark.hardware(
            id=metadata.get("id", default_id), component=metadata.get("component", "custom"),
            layer=metadata.get("layer", "headless"), instructions=metadata.get("instructions", ""),
        ))
        if metadata.get("active"):
            item.add_marker(pytest.mark.active(metadata["active"]))
        item.script_options = metadata
        yield item


class ShellItem(pytest.Item):
    def runtest(self):
        result = run_command(*script_command(self.path), timeout=self.script_options.get("timeout", 10))
        self._yb_evidence = {"command": list(result.args), "returncode": result.returncode,
                             "stdout": result.stdout, "stderr": result.stderr}
        assert_script_result(result)

    def reportinfo(self):
        return self.path, 0, f"shell: {self.name}"
