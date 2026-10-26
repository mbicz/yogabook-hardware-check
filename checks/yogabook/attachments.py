"""Board-local attachment discovery; no bus probes, mounts or policy writes."""
from pathlib import Path
import re

from checks.yogabook.platform import identity, text


def usb_function(pci_class: str) -> str | None:
    """PCI programming interface matters: xDCI is not a root-hub host."""
    value = int(pci_class, 16) if pci_class else 0
    if value >> 8 != 0x0c03:
        return None
    return "device" if value & 0xff == 0xfe else "host"


def usb_controllers(root=Path("/sys/bus/pci/devices")):
    records = []
    for path in sorted(root.glob("*")):
        pci_class = text(path / "class")
        function = usb_function(pci_class)
        if function:
            records.append({**identity(path), "class": pci_class, "function": function,
                            "vendor": text(path / "vendor"), "device": text(path / "device")})
    return records


def usb_peripherals(root=Path("/sys/bus/usb/devices")):
    # Interfaces (1-1:1.0) and root hubs (usb1) are not external devices.
    return [{**identity(path), "vendor": text(path / "idVendor"),
             "product": text(path / "idProduct"), "name": text(path / "product")}
            for path in sorted(root.glob("*")) if re.fullmatch(r"\d+-\d+(?:\.\d+)*", path.name)
            and text(path / "bDeviceClass") != "09"]


def sd_cards(root=Path("/sys/bus/mmc/devices")):
    return [path for path in sorted(root.glob("*")) if text(path / "type") == "SD"]


def hdmi_connectors(root=Path("/sys/class/drm")):
    return [path for path in sorted(root.glob("card*-HDMI-A-*"))]


def firmware_files(patterns, roots=(Path("/lib/firmware"), Path("/usr/lib/firmware"))):
    """Include updates/compressed firmware; report real paths without copying bytes."""
    return sorted({str(path.resolve()) for root in roots for prefix in ("", "updates/")
                   for pattern in patterns for suffix in ("", ".xz", ".zst")
                   for path in root.glob(prefix + pattern + suffix) if path.is_file()})
