#!/usr/bin/env python3
"""CUPS backend helper for a Brother QL-800 and 50/62 mm continuous tape."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO, Sequence

from PIL import Image, ImageFilter, ImageOps


DEFAULT_CONFIG = "/etc/formularkopf-klebchen.json"
LOCK_PATH = "/var/lib/formularkopf-klebchen/usb.lock"
MODEL = "QL-800"
USB_VENDOR = 0x04F9
USB_PRODUCT = 0x209B
USB_URI_RE = re.compile(
    r"usb://0x04f9:0x209b(?:/([A-Za-z0-9._~-]+))?\Z", re.IGNORECASE
)

DEVICE_DOTS = 720
MEDIA_DOTS = 590
PRINTABLE_DOTS = 554
RIGHT_OFFSET_DOTS = 12
SIDE_MARGIN_DOTS = (MEDIA_DOTS - PRINTABLE_DOTS) // 2
FEED_MARGIN_DOTS = 35
TARGET_DPI = 300
# Physical width, printable width, right print-head offset (Brother raster manual).
MEDIA_SPECS = {50: (590, 554, 12), 62: (732, 696, 12)}

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_PAGES = 20
MAX_COPIES = 100
GS_TIMEOUT_SECONDS = 90
HEADER_LIMIT = 8192

POWER_OFF_MINUTES = (0, 10, 20, 30, 40, 50, 60)
POWER_STATUS_TIMEOUT_SECONDS = 3
STATUS_REQUEST = b"\x1b\x69\x53"
RASTER_MODE = b"\x1b\x69\x61\x01"
GET_AUTO_POWER_OFF = b"\x1b\x69\x55\x41\x01"
SET_AUTO_POWER_OFF = b"\x1b\x69\x55\x41\x00"

CUPS_BACKEND_OK = 0
CUPS_BACKEND_FAILED = 1
CUPS_BACKEND_HOLD = 3
CUPS_BACKEND_STOP = 4
CUPS_BACKEND_CANCEL = 5

CONFIG_KEYS = {
    "printer_uri",
    "render_dpi",
    "crop_left_pt",
    "crop_top_pt",
    "crop_width_mm",
    "crop_height_mm",
    "rotation",
    "label_length_mm",
    "threshold",
}


class BackendError(Exception):
    """Base class for errors safe to report without job data."""


class ConfigurationError(BackendError):
    pass


class InputError(BackendError):
    pass


class RenderError(BackendError):
    pass


class PrinterError(BackendError):
    pass


class UnconfirmedPrintError(PrinterError):
    pass


def _number(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} muss eine Zahl sein")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ConfigurationError(
            f"{name} muss zwischen {minimum:g} und {maximum:g} liegen"
        )
    return result


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} muss eine ganze Zahl sein")
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} muss zwischen {minimum} und {maximum} liegen"
        )
    return value


def validate_config(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ConfigurationError("Konfiguration muss ein JSON-Objekt sein")
    missing = CONFIG_KEYS - raw.keys()
    extra = raw.keys() - CONFIG_KEYS - {"media_width_mm"}
    if missing:
        raise ConfigurationError("Fehlende Konfigurationsfelder: " + ", ".join(sorted(missing)))
    if extra:
        raise ConfigurationError("Unbekannte Konfigurationsfelder: " + ", ".join(sorted(extra)))

    uri = raw["printer_uri"]
    if not isinstance(uri, str) or USB_URI_RE.fullmatch(uri) is None:
        raise ConfigurationError(
            "printer_uri muss usb://0x04f9:0x209b oder diese URI mit /SERIAL sein"
        )
    render_dpi = _integer(raw["render_dpi"], "render_dpi", 300, 1200)
    left = _number(raw["crop_left_pt"], "crop_left_pt", 0, 2000)
    top = _number(raw["crop_top_pt"], "crop_top_pt", 0, 2000)
    width = _number(raw["crop_width_mm"], "crop_width_mm", 10, 1000)
    height = _number(raw["crop_height_mm"], "crop_height_mm", 10, 100)
    rotation = _integer(raw["rotation"], "rotation", 90, 270)
    if rotation not in (90, 270):
        raise ConfigurationError("rotation muss 90 oder 270 sein")
    length = _number(raw["label_length_mm"], "label_length_mm", 20, 1000)
    threshold = _integer(raw["threshold"], "threshold", 0, 255)
    media_width = _integer(raw.get("media_width_mm", 50), "media_width_mm", 50, 62)
    if media_width not in MEDIA_SPECS:
        raise ConfigurationError("media_width_mm muss 50 oder 62 sein (Endlosrolle)")

    total_length_dots = round(length / 25.4 * TARGET_DPI)
    raster_length = total_length_dots - 2 * FEED_MARGIN_DOTS
    if raster_length < 150 or raster_length > 11811:
        raise ConfigurationError("label_length_mm liegt außerhalb des QL-800-Bereichs")

    return {
        "printer_uri": uri,
        "render_dpi": render_dpi,
        "crop_left_pt": left,
        "crop_top_pt": top,
        "crop_width_mm": width,
        "crop_height_mm": height,
        "rotation": rotation,
        "label_length_mm": length,
        "threshold": threshold,
        "media_width_mm": media_width,
    }


def load_config(path: str | os.PathLike[str]) -> dict[str, object]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Konfiguration kann nicht gelesen werden") from exc
    return validate_config(raw)


def _bounded_read(stream: BinaryIO) -> bytes:
    data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise InputError(f"Eingabe überschreitet {MAX_INPUT_BYTES} Bytes")
    if not data:
        raise InputError("Eingabe ist leer")
    return data


def read_input(path: str | None) -> bytes:
    if path is None:
        return _bounded_read(sys.stdin.buffer)
    try:
        with open(path, "rb") as stream:
            return _bounded_read(stream)
    except OSError as exc:
        raise InputError("Eingabedatei kann nicht gelesen werden") from exc


def unwrap_document(data: bytes) -> tuple[bytes, str]:
    """Accept PDF/PS and remove only a bounded PJL/UEL prefix/suffix."""
    candidates = [(data.find(b"%PDF-", 0, HEADER_LIMIT), "pdf"),
                  (data.find(b"%!PS", 0, HEADER_LIMIT), "ps")]
    candidates = [(position, kind) for position, kind in candidates if position >= 0]
    if not candidates:
        raise InputError("Nur PDF oder PostScript wird unterstützt")
    position, kind = min(candidates)
    prefix = data[:position].strip(b"\x04 \t\r\n")
    if prefix and not (prefix.startswith(b"\x1b%-12345X") or prefix.lstrip().startswith(b"@PJL")):
        raise InputError("Unerwartete Daten vor dem PDF/PostScript-Header")
    document = data[position:].rstrip(b"\x04")
    # Windows drivers commonly append a UEL/PJL trailer. Removing it keeps the
    # parser input deterministic without scanning unbounded content.
    trailer_start = document.rfind(b"\x1b%-12345X", max(0, len(document) - HEADER_LIMIT))
    if trailer_start > 0:
        document = document[:trailer_start]
    return document, kind


def _limit_ghostscript() -> None:
    """Best-effort process limits; called only in the Ghostscript child."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (GS_TIMEOUT_SECONDS, GS_TIMEOUT_SECONDS + 1))
        memory = 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_INPUT_BYTES * 4, MAX_INPUT_BYTES * 4))
    except (AttributeError, ImportError, OSError, ValueError):
        pass


def _render_page_files(
    document: bytes, config: dict[str, object], directory: Path
) -> list[Path]:
    ghostscript = shutil.which("gs")
    if ghostscript is None:
        raise RenderError("Ghostscript ist nicht verfügbar")
    input_path = directory / "input"
    input_path.write_bytes(document)
    pattern = directory / "page-%03d.png"
    command = [
        ghostscript,
        "-q",
        "-dNOPAUSE",
        "-dBATCH",
        "-dSAFER",
        "-dTextAlphaBits=4",
        "-dGraphicsAlphaBits=4",
        "-dDOINTERPOLATE",
        f"-dLastPage={MAX_PAGES + 1}",
        "-sDEVICE=pnggray",
        "-sPAPERSIZE=a4",
        f"-r{config['render_dpi']}",
        f"-sOutputFile={pattern}",
        "-f",
        str(input_path),
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=GS_TIMEOUT_SECONDS,
            check=False,
            preexec_fn=_limit_ghostscript if os.name == "posix" else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RenderError("Ghostscript konnte die Eingabe nicht rechtzeitig rendern") from exc
    if completed.returncode != 0:
        # Do not forward stderr: PostScript errors may contain patient/job data.
        raise RenderError(f"Ghostscript-Fehler (Exitcode {completed.returncode})")

    paths = sorted(directory.glob("page-*.png"))
    if not paths:
        raise RenderError("Das Dokument enthält keine renderbare Seite")
    if len(paths) > MAX_PAGES:
        raise RenderError(f"Das Dokument hat mehr als {MAX_PAGES} Seiten")

    # Validate all output before any caller can start printing. verify() avoids
    # retaining every high-resolution full page in memory at once.
    try:
        for path in paths:
            with Image.open(path) as source:
                source.verify()
    except (OSError, ValueError) as exc:
        raise RenderError("Gerenderte Seite ist ungültig") from exc
    return paths


def render_pages(document: bytes, config: dict[str, object], directory: Path) -> list[Path]:
    paths = _render_page_files(document, config, directory)
    # Validate headers first; only one full A4 raster is loaded at a time later.
    # This keeps multi-page jobs usable on Raspberry Pis with limited RAM.
    dpi = int(config["render_dpi"])
    expected = (round(210 / 25.4 * dpi), round(297 / 25.4 * dpi))
    tolerance = math.ceil(dpi / 25.4)  # at most 1 mm rounding/driver difference
    try:
        for path in paths:
            with Image.open(path) as source:
                if any(abs(actual - target) > tolerance
                       for actual, target in zip(source.size, expected)):
                    raise RenderError("Eingabe muss A4 im Hochformat sein (Client-Treiber prüfen)")
                source.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise RenderError("Gerenderte Seite ist ungültig") from exc
    return paths


def crop_page(page: Image.Image, config: dict[str, object]) -> Image.Image:
    dpi = int(config["render_dpi"])
    left = round(float(config["crop_left_pt"]) * dpi / 72.0)
    top = round(float(config["crop_top_pt"]) * dpi / 72.0)
    width = round(float(config["crop_width_mm"]) / 25.4 * dpi)
    height = round(float(config["crop_height_mm"]) / 25.4 * dpi)
    if left + width > page.width or top + height > page.height:
        raise RenderError("Konfigurierter Ausschnitt liegt außerhalb der Seite")
    return page.crop((left, top, left + width, top + height))


def _resampling_lanczos() -> int:
    resampling = getattr(Image, "Resampling", Image)
    return resampling.LANCZOS


def prepare_bitmap(page: Image.Image, config: dict[str, object]) -> Image.Image:
    """Fit the entire crop proportionally inside the hardware's printable area."""
    crop = ImageOps.autocontrast(crop_page(page, config).convert("L"))
    rotated = crop.rotate(int(config["rotation"]), expand=True)
    total_length = round(float(config["label_length_mm"]) / 25.4 * TARGET_DPI)
    printable_height = total_length - 2 * FEED_MARGIN_DOTS
    scale = min(PRINTABLE_DOTS / rotated.width, printable_height / rotated.height)
    size = (max(1, round(rotated.width * scale)), max(1, round(rotated.height * scale)))
    fitted = rotated.resize(size, _resampling_lanczos())
    fitted = fitted.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=2))
    printable = Image.new("L", (PRINTABLE_DOTS, printable_height), 255)
    printable.paste(fitted, ((PRINTABLE_DOTS - size[0]) // 2,
                            (printable_height - size[1]) // 2))
    threshold = int(config["threshold"])
    return printable.point(lambda value: 0 if value < threshold else 255, mode="1")


def physical_preview(printable: Image.Image, config: dict[str, object]) -> Image.Image:
    total_length = round(float(config["label_length_mm"]) / 25.4 * TARGET_DPI)
    media_dots = MEDIA_SPECS[int(config.get("media_width_mm", 50))][0]
    result = Image.new("1", (media_dots, total_length), 1)
    result.paste(printable, ((media_dots - printable.width) // 2, FEED_MARGIN_DOTS))
    return result


def raster_instructions(printable: Image.Image, media_width_mm: int = 50) -> bytes:
    if media_width_mm not in MEDIA_SPECS:
        raise ConfigurationError("Nur 50- und 62-mm-Endlosrollen werden unterstützt")
    expected_height_min = 150
    if not (
        printable.width == PRINTABLE_DOTS
        and expected_height_min <= printable.height <= 11811
    ):
        raise RenderError("Ungültige Rasterabmessungen")
    canvas = Image.new("1", (DEVICE_DOTS, printable.height), 1)
    _, roll_printable, right_offset = MEDIA_SPECS[media_width_mm]
    # Keep the existing label content at its physical size; only add white space.
    left = DEVICE_DOTS - roll_printable - right_offset + (roll_printable - PRINTABLE_DOTS) // 2
    canvas.paste(printable, (left, 0))

    try:
        from brother_ql.raster import BrotherQLRaster

        qlr = BrotherQLRaster(MODEL)
        qlr.exception_on_warning = True
        qlr.add_switch_mode()
        qlr.add_invalidate()
        qlr.add_initialize()
        qlr.add_switch_mode()
        qlr.add_status_information()
        qlr.mtype = 0x0A
        qlr.mwidth = media_width_mm
        qlr.mlength = 0
        qlr.pquality = True
        qlr.add_media_and_quality(canvas.height)
        qlr.add_autocut(True)
        qlr.add_cut_every(1)
        qlr.dpi_600 = False
        qlr.cut_at_end = True
        qlr.two_color_printing = False
        qlr.add_expanded_mode()
        qlr.add_margins(FEED_MARGIN_DOTS)
        # Brother raster bits mean ink=1, whereas Pillow uses white=1.
        qlr.add_raster_data(ImageOps.invert(canvas.convert("L")).convert("1"))
        qlr.add_print(last_page=True)
        return bytes(qlr.data)
    except BackendError:
        raise
    except Exception as exc:
        raise RenderError("Brother-Rasterdaten konnten nicht erzeugt werden") from exc


def _device_serial(device: object) -> str | None:
    try:
        value = getattr(device, "serial_number")
    except Exception:
        return None
    if value is None:
        return None
    return str(value)


def discover_devices() -> list[tuple[str, object]]:
    try:
        import usb.core

        candidates = list(usb.core.find(find_all=True, idVendor=USB_VENDOR, idProduct=USB_PRODUCT))
    except Exception as exc:
        raise PrinterError("USB-Druckersuche ist fehlgeschlagen") from exc

    found: list[tuple[str, object]] = []
    for device in candidates:
        try:
            vendor = int(getattr(device, "idVendor"))
            product = int(getattr(device, "idProduct"))
        except (TypeError, ValueError, AttributeError):
            continue
        if vendor != USB_VENDOR or product != USB_PRODUCT:
            continue
        serial = _device_serial(device)
        uri = "usb://0x04f9:0x209b" + (f"/{serial}" if serial else "")
        found.append((uri, device))
    found.sort(key=lambda item: item[0])
    return found


def list_printer_uris() -> list[str]:
    return [uri for uri, _device in discover_devices()]


def resolve_device(uri: str) -> object:
    match = USB_URI_RE.fullmatch(uri)
    if match is None:
        raise ConfigurationError("Ungültige QL-800-USB-URI")
    requested_serial = match.group(1)
    devices = discover_devices()
    if requested_serial is not None:
        expected = f"usb://0x04f9:0x209b/{requested_serial}"
        matches = [device for found_uri, device in devices if found_uri == expected]
    else:
        matches = [device for _found_uri, device in devices]
    if not matches:
        raise PrinterError("Konfigurierter Brother QL-800 wurde nicht gefunden")
    if len(matches) != 1:
        raise PrinterError("Mehrere QL-800 gefunden; Seriennummer in printer_uri erforderlich")
    return matches[0]


def decode_printer_status(data: bytes) -> dict[str, object]:
    """Decode a QL-800's 32-byte status, without document or user information."""
    if len(data) != 32 or data[:5] != b"\x80\x20\x42\x34\x38":
        raise PrinterError("Ungültige Statusantwort des QL-800")
    error_names = (
        (8, 0x01, "Keine Rolle eingelegt"),
        (8, 0x02, "Rolle leer"),
        (8, 0x04, "Schneideeinheit blockiert"),
        (8, 0x10, "Drucker beschäftigt"),
        (8, 0x20, "Drucker ausgeschaltet"),
        (9, 0x01, "Rolle passt nicht zum Druckauftrag"),
        (9, 0x02, "Druckspeicher voll"),
        (9, 0x04, "Kommunikationsfehler"),
        (9, 0x10, "Abdeckung offen"),
        (9, 0x40, "Vorschubfehler oder Rolle leer"),
        (9, 0x80, "Systemfehler"),
    )
    errors = [message for offset, mask, message in error_names if data[offset] & mask]
    if (data[8] or data[9]) and not errors:
        errors.append("Unbekannter Gerätefehler")
    # The QL-800 manual lists 4A/4B, but the connected QL-800 also returns 0A.
    # Accept the explicit media codes; do not mask arbitrary unknown values.
    media_types = {0: "keine Rolle", 0x0A: "Endlosrolle", 0x4A: "Endlosrolle",
                   0x0B: "Einzeletiketten", 0x4B: "Einzeletiketten"}
    return {
        "media_width_mm": data[10],
        "media_length_mm": data[17],
        "media_type": media_types.get(data[11], f"unbekannt (0x{data[11]:02x})"),
        "media_type_code": data[11],
        "status_type": data[18],
        "phase_type": data[19],
        "error_code": f"{data[8]:02x}:{data[9]:02x}",
        "errors": errors,
    }


def query_printer_status(device: object) -> dict[str, object]:
    """Request fresh roll/status information without feeding, cutting or printing."""
    printer = None
    try:
        from brother_ql.backends.pyusb import BrotherQLBackendPyUSB
        from usb.core import USBTimeoutError

        printer = BrotherQLBackendPyUSB(device)
        printer.write(STATUS_REQUEST)
        deadline = time.monotonic() + 3
        buffer = bytearray()
        while time.monotonic() < deadline:
            try:
                buffer.extend(printer.read_dev.read(32, timeout=200))
            except USBTimeoutError:
                continue
            while len(buffer) >= 32:
                status = decode_printer_status(bytes(buffer[:32]))
                del buffer[:32]
                # Discard old phase/completion notifications; wait for our reply.
                if status["status_type"] == 0:
                    return status
        raise PrinterError("Keine aktuelle USB-Statusantwort; Verbindung und Editor Lite prüfen")
    except PrinterError:
        raise
    except Exception as exc:
        raise PrinterError("USB-Statusabfrage fehlgeschlagen; Verbindung und Zugriffsrechte prüfen") from exc
    finally:
        if printer is not None:
            printer.dispose()


def _read_power_frame(
    printer: object, buffer: bytearray, expected_type: int
) -> tuple[bytes, dict[str, object]]:
    """Read and validate one requested QL-800 response, ignoring notifications."""
    from usb.core import USBTimeoutError

    deadline = time.monotonic() + POWER_STATUS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            buffer.extend(printer.read_dev.read(32, timeout=200))
        except USBTimeoutError:
            continue
        while len(buffer) >= 32:
            frame = bytes(buffer[:32])
            del buffer[:32]
            status = decode_printer_status(frame)
            if status["errors"]:
                raise PrinterError("Druckerstatus: " + "; ".join(status["errors"]))
            if status["status_type"] == expected_type:
                return frame, status
    raise PrinterError("Keine aktuelle USB-Antwort zur Energieeinstellung")


def _request_power_status(
    printer: object, buffer: bytearray
) -> dict[str, object]:
    printer.write(STATUS_REQUEST)
    _frame, status = _read_power_frame(printer, buffer, 0)
    if status["phase_type"] != 0:
        raise PrinterError("Drucker ist noch beschäftigt")
    return status


def _read_auto_power_off(printer: object, buffer: bytearray) -> int:
    printer.write(GET_AUTO_POWER_OFF)
    frame, _status = _read_power_frame(printer, buffer, 0xF0)
    value = frame[30]
    if frame[31] != 1:
        raise PrinterError("Energieeinstellung wurde vom QL-800 nicht bestätigt")
    if value > 6:
        raise PrinterError("Ungültiger Wert der QL-800-Energieeinstellung")
    return value * 10


def _set_auto_power_off(printer: object, minutes: int) -> None:
    value = minutes // 10
    printer.write(SET_AUTO_POWER_OFF + bytes((value,)))


def _power_off_transaction(device: object, minutes: int | None) -> dict[str, object]:
    """Read or change power-off on one resolved device and always release USB."""
    printer = None
    try:
        from brother_ql.backends.pyusb import BrotherQLBackendPyUSB

        printer = BrotherQLBackendPyUSB(device)
        buffer = bytearray()
        _request_power_status(printer, buffer)
        printer.write(RASTER_MODE)
        previous = _read_auto_power_off(printer, buffer)
        current = previous
        changed = minutes is not None and minutes != previous
        if changed:
            _set_auto_power_off(printer, minutes)
            current = _read_auto_power_off(printer, buffer)
            if current != minutes:
                raise PrinterError("QL-800-Energieeinstellung konnte nicht bestätigt werden")
            _request_power_status(printer, buffer)

        result: dict[str, object] = {
            "auto_power_off_minutes": current,
            "automatic_shutdown_disabled": current == 0,
        }
        if minutes is not None:
            result["previous_auto_power_off_minutes"] = previous
            result["changed"] = changed
        return result
    except PrinterError:
        raise
    except Exception as exc:
        raise PrinterError(
            "USB-Energieeinstellung fehlgeschlagen; Verbindung und Zugriffsrechte prüfen"
        ) from exc
    finally:
        if printer is not None:
            active_error = sys.exc_info()[0] is not None
            try:
                printer.dispose()
            except Exception as exc:
                if not active_error:
                    raise PrinterError("USB-Verbindung konnte nicht freigegeben werden") from exc


def show_or_set_auto_power_off(config_path: str, minutes: int | None) -> int:
    if minutes is not None and minutes not in POWER_OFF_MINUTES:
        raise ConfigurationError("Automatische Abschaltung muss 0, 10, 20, 30, 40, 50 oder 60 sein")
    config = load_config(config_path)
    try:
        with open(LOCK_PATH, "a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PrinterError("Drucker wird gerade von einem anderen Auftrag verwendet") from exc
            try:
                device = resolve_device(str(config["printer_uri"]))
                result = _power_off_transaction(device, minutes)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except PrinterError:
        raise
    except OSError as exc:
        raise PrinterError("USB-Sperrdatei kann nicht geöffnet werden") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def check_media(status: dict[str, object], expected_width: int) -> None:
    actual = status["media_width_mm"]
    kind = status["media_type"]
    if status["media_type_code"] not in (0x0A, 0x4A) or status["media_length_mm"] != 0:
        raise PrinterError(f"Erkannt: {kind}, {actual} mm; benötigt: {expected_width}-mm-Endlosrolle")
    if actual != expected_width:
        raise PrinterError(
            f"Rollenbreite: Drucker erkennt {actual} mm, eingestellt sind {expected_width} mm. "
            "media_width_mm in /etc/formularkopf-klebchen.json anpassen oder passende Rolle einlegen"
        )
    if status["errors"]:
        raise PrinterError("Druckerstatus: " + "; ".join(status["errors"]))
    if status["phase_type"] != 0:
        raise PrinterError("Drucker ist noch beschäftigt; Auftrag wird angehalten")


def show_printer_status(config_path: str) -> int:
    config = load_config(config_path)
    try:
        with open(LOCK_PATH, "a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PrinterError("Drucker wird gerade von einem anderen Auftrag verwendet") from exc
            try:
                status = query_printer_status(resolve_device(str(config["printer_uri"])))
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise PrinterError("USB-Sperrdatei kann nicht geöffnet werden") from exc
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def send_label(instructions: bytes, device: object) -> None:
    try:
        from brother_ql.backends.helpers import send

        status = send(
            instructions,
            printer_identifier=device,
            backend_identifier="pyusb",
            blocking=True,
        )
    except Exception as exc:
        raise UnconfirmedPrintError("USB-Übertragung nicht eindeutig bestätigt") from exc
    if not isinstance(status, dict):
        raise UnconfirmedPrintError("Druckstatus fehlt")
    if status.get("outcome") == "error":
        state = status.get("printer_state") or {}
        width = state.get("media_width", "unbekannt")
        raise UnconfirmedPrintError(f"Drucker meldet einen Medien- oder Gerätefehler (erkannte Breite: {width} mm)")
    if status.get("did_print") is not True or status.get("ready_for_next_job") is not True:
        raise UnconfirmedPrintError("Druck wurde nicht vollständig bestätigt")


def _own_device_uri() -> None:
    device_uri = os.environ.get("DEVICE_URI")
    if device_uri and device_uri != "formularkopf-klebchen://ql800":
        raise ConfigurationError("DEVICE_URI gehört nicht zu diesem CUPS-Backend")


def process_document(data: bytes, config: dict[str, object]) -> list[Image.Image]:
    document, _kind = unwrap_document(data)
    with tempfile.TemporaryDirectory(prefix="formularkopf-klebchen-") as temporary:
        paths = _render_page_files(document, config, Path(temporary))
        bitmaps: list[Image.Image] = []
        for path in paths:
            try:
                with Image.open(path) as source:
                    source.load()
                    bitmaps.append(prepare_bitmap(source.convert("L"), config))
            except (OSError, ValueError) as exc:
                raise RenderError("Gerenderte Seite ist ungültig") from exc
        # Every page is rendered, validated and prepared before USB access.
        return bitmaps


def preview(config_path: str, input_path: str, output_directory: str) -> int:
    config = load_config(config_path)
    bitmaps = process_document(read_input(input_path), config)
    output = Path(output_directory)
    try:
        output.mkdir(parents=True, exist_ok=True)
        for number, bitmap in enumerate(bitmaps, 1):
            path = output / f"page-{number:03d}.png"
            if path.exists():
                raise InputError("Preview-Zieldatei existiert bereits")
            physical_preview(bitmap, config).save(path, format="PNG")
    except OSError as exc:
        raise InputError("Preview kann nicht geschrieben werden") from exc
    return 0


def cups_print(arguments: Sequence[str], config_path: str = DEFAULT_CONFIG) -> int:
    if len(arguments) not in (5, 6):
        raise InputError("CUPS-Aufruf erwartet job-id user title copies options [file]")
    _own_device_uri()
    try:
        copies = int(arguments[3], 10)
    except ValueError as exc:
        raise InputError("Ungültige Kopienzahl") from exc
    if not 1 <= copies <= MAX_COPIES:
        raise InputError(f"Kopienzahl muss zwischen 1 und {MAX_COPIES} liegen")

    config = load_config(config_path)
    data = read_input(arguments[5] if len(arguments) == 6 else None)
    bitmaps = process_document(data, config)
    media_width = int(config.get("media_width_mm", 50))
    instructions = [raster_instructions(bitmap, media_width) for bitmap in bitmaps]

    try:
        lock = open(LOCK_PATH, "a+b")
    except OSError as exc:
        raise PrinterError("USB-Sperrdatei kann nicht geöffnet werden") from exc
    with lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            device = resolve_device(str(config["printer_uri"]))
            check_media(query_printer_status(device), media_width)
            for _copy in range(copies):
                for payload in instructions:
                    send_label(payload, device)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return CUPS_BACKEND_OK


def discovery_line() -> str:
    return (
        'direct formularkopf-klebchen://ql800 '
        '"Brother QL-800 Formularkopf" "Brother QL-800 Formularkopf"'
    )


def _standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-config", action="store_true")
    action.add_argument("--list-printers", action="store_true")
    action.add_argument("--printer-status", action="store_true")
    action.add_argument("--power-off-status", action="store_true")
    action.add_argument(
        "--set-auto-power-off",
        type=int,
        choices=POWER_OFF_MINUTES,
        metavar="MINUTES",
    )
    action.add_argument("--preview", nargs=2, metavar=("INPUT", "OUTPUT_DIR"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    standalone = bool(arguments and arguments[0].startswith("--"))
    try:
        if not arguments:
            print(discovery_line())
            return CUPS_BACKEND_OK
        if standalone:
            options = _standalone_parser().parse_args(arguments)
            if options.list_printers:
                print(json.dumps(list_printer_uris(), ensure_ascii=True))
            elif options.check_config:
                load_config(options.config)
                print("OK")
            elif options.printer_status:
                return show_printer_status(options.config)
            elif options.power_off_status:
                return show_or_set_auto_power_off(options.config, None)
            elif options.set_auto_power_off is not None:
                return show_or_set_auto_power_off(options.config, options.set_auto_power_off)
            else:
                return preview(options.config, options.preview[0], options.preview[1])
            return 0
        return cups_print(arguments)
    except UnconfirmedPrintError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return CUPS_BACKEND_HOLD
    except PrinterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1 if standalone else CUPS_BACKEND_HOLD
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1 if standalone else CUPS_BACKEND_STOP
    except (InputError, RenderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1 if standalone else CUPS_BACKEND_CANCEL
    except Exception:
        # Never report success or expose document-related exception details.
        print("ERROR: Unerwarteter Backend-Fehler", file=sys.stderr)
        return 1 if standalone else CUPS_BACKEND_STOP


if __name__ == "__main__":
    raise SystemExit(main())
