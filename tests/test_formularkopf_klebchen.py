from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "formularkopf_klebchen", ROOT / "ql800" / "formularkopf_klebchen.py"
)
backend = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(backend)


def working_ghostscript():
    executable = shutil.which("gs")
    if executable is None:
        return False
    try:
        return subprocess.run(
            [executable, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    except OSError:
        return False


GS_WORKS = working_ghostscript()


def config(**changes):
    value = {
        "printer_uri": "usb://0x04f9:0x209b/A1B2C3",
        "render_dpi": 300,
        "crop_left_pt": 26.6,
        "crop_top_pt": 16.5,
        "crop_width_mm": 82.0,
        "crop_height_mm": 50.0,
        "rotation": 270,
        "label_length_mm": 82.0,
        "threshold": 188,
    }
    value.update(changes)
    return backend.validate_config(value)


def raw_status(width=50, kind=0x4A, length=0, error1=0, error2=0, status_type=0):
    data = bytearray(32)
    data[:5] = b"\x80\x20\x42\x34\x38"
    data[8:12] = bytes((error1, error2, width, kind))
    data[17] = length
    data[18] = status_type
    return bytes(data)


def raw_power(value, ack=1, **status_changes):
    data = bytearray(raw_status(status_type=0xF0, **status_changes))
    data[30] = value
    data[31] = ack
    return bytes(data)


def status_reply(**changes):
    return backend.decode_printer_status(raw_status(**changes))


def page_for_crop(cfg, fill=255):
    dpi = cfg["render_dpi"]
    right = round(cfg["crop_left_pt"] * dpi / 72) + round(
        cfg["crop_width_mm"] / 25.4 * dpi
    )
    bottom = round(cfg["crop_top_pt"] * dpi / 72) + round(
        cfg["crop_height_mm"] / 25.4 * dpi
    )
    return Image.new("L", (right + 20, bottom + 20), fill)


class ConfigurationTests(unittest.TestCase):
    def test_valid_configuration_is_normalized(self):
        value = config()
        self.assertEqual(value["render_dpi"], 300)
        self.assertEqual(value["rotation"], 270)

    def test_rejects_wrong_device_unknown_keys_and_bool_numbers(self):
        bad_values = [
            {"printer_uri": "usb://0x04f9:0x2042/OTHER"},
            {"render_dpi": True},
            {"rotation": 180},
            {"crop_width_mm": -1.0},
            {"crop_height_mm": 200.0},
            {"threshold": 256},
            {"surprise": 1},
        ]
        for change in bad_values:
            with self.subTest(change=change), self.assertRaises(backend.ConfigurationError):
                config(**change)

    def test_media_width_is_backward_compatible_and_rejects_guessed_sizes(self):
        self.assertEqual(config()["media_width_mm"], 50)
        self.assertEqual(config(media_width_mm=62)["media_width_mm"], 62)
        for invalid in (60, 54, "62", True, 62.0):
            with self.subTest(width=invalid), self.assertRaises(backend.ConfigurationError):
                config(media_width_mm=invalid)

    def test_check_config_does_not_touch_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(dict(config())), encoding="utf-8")
            with mock.patch.object(backend, "discover_devices", side_effect=AssertionError):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    self.assertEqual(backend.main(["--config", str(path), "--check-config"]), 0)
            self.assertEqual(stdout.getvalue(), "OK\n")


class InputTests(unittest.TestCase):
    def test_accepts_plain_and_pjl_wrapped_formats(self):
        pdf, kind = backend.unwrap_document(b"%PDF-1.4\n%%EOF\n")
        self.assertEqual(kind, "pdf")
        self.assertTrue(pdf.startswith(b"%PDF-"))

        wrapped = (
            b"\x1b%-12345X@PJL JOB\r\n@PJL ENTER LANGUAGE=POSTSCRIPT\r\n"
            b"%!PS-Adobe-3.0\nshowpage\n%%EOF\n\x1b%-12345X@PJL EOJ\r\n"
        )
        ps, kind = backend.unwrap_document(wrapped)
        self.assertEqual(kind, "ps")
        self.assertTrue(ps.startswith(b"%!PS"))
        self.assertTrue(ps.endswith(b"%%EOF\n"))

    def test_windows_control_d_wrapper(self):
        document, kind = backend.unwrap_document(b"\x04%!PS-Adobe-3.0\nshowpage\n\x04")
        self.assertEqual(kind, "ps")
        self.assertEqual(document, b"%!PS-Adobe-3.0\nshowpage\n")

    def test_rejects_unknown_or_unbounded_headers(self):
        values = (b"hello", b"garbage%!PS\n", b"\x1b%-12345X" + b"x" * 9000 + b"%!PS")
        for data in values:
            with self.subTest(data=data[:20]), self.assertRaises(backend.InputError):
                backend.unwrap_document(data)

    def test_bounded_reader_rejects_oversize_and_empty(self):
        with self.assertRaises(backend.InputError):
            backend._bounded_read(io.BytesIO(b""))
        with mock.patch.object(backend, "MAX_INPUT_BYTES", 3):
            with self.assertRaises(backend.InputError):
                backend._bounded_read(io.BytesIO(b"1234"))


class GeometryTests(unittest.TestCase):
    def test_physical_dimensions_and_symmetric_unprintable_margins(self):
        cfg = config()
        page = page_for_crop(cfg, fill=0)
        printable = backend.prepare_bitmap(page, cfg)
        self.assertEqual(printable.size, (554, 899))
        self.assertEqual(printable.mode, "1")
        ink = ImageOps.invert(printable.convert("L")).getbbox()
        self.assertEqual(ink, (3, 0, 551, 899))  # fit without stretching/cropping

        preview = backend.physical_preview(printable, cfg)
        self.assertEqual(preview.size, (590, 969))
        self.assertEqual(preview.crop((0, 0, 590, 35)).getbbox(), (0, 0, 590, 35))
        self.assertEqual(preview.crop((0, 934, 590, 969)).getbbox(), (0, 0, 590, 35))
        self.assertEqual(preview.crop((0, 0, 18, 969)).getbbox(), (0, 0, 18, 969))
        self.assertEqual(preview.crop((572, 0, 590, 969)).getbbox(), (0, 0, 18, 969))
        self.assertEqual(ImageOps.invert(preview.convert("L")).getbbox(), (21, 35, 569, 934))

    def test_asymmetric_crop_and_rotation_direction(self):
        cfg270 = config(rotation=270)
        page = page_for_crop(cfg270)
        left = round(cfg270["crop_left_pt"] * 300 / 72)
        top = round(cfg270["crop_top_pt"] * 300 / 72)
        crop_width = round(82 / 25.4 * 300)
        crop_height = round(50 / 25.4 * 300)
        draw = ImageDraw.Draw(page)
        draw.rectangle(
            (left, top, left + crop_width // 8, top + crop_height // 8), fill=0
        )
        result270 = backend.prepare_bitmap(page, cfg270)
        result90 = backend.prepare_bitmap(page, config(rotation=90))

        def black_centroid(image):
            points = [
                (x, y)
                for y in range(image.height)
                for x in range(image.width)
                if image.getpixel((x, y)) == 0
            ]
            return (
                sum(x for x, _ in points) / len(points),
                sum(y for _, y in points) / len(points),
            )

        x270, y270 = black_centroid(result270)
        x90, y90 = black_centroid(result90)
        self.assertGreater(x270, result270.width / 2)
        self.assertLess(y270, result270.height / 2)
        self.assertLess(x90, result90.width / 2)
        self.assertGreater(y90, result90.height / 2)

    def test_all_four_crop_edges_survive_fitting(self):
        cfg = config()
        page = page_for_crop(cfg)
        left = round(cfg["crop_left_pt"] * 300 / 72)
        top = round(cfg["crop_top_pt"] * 300 / 72)
        width = round(82 / 25.4 * 300)
        height = round(50 / 25.4 * 300)
        draw = ImageDraw.Draw(page)
        draw.rectangle((left, top, left + width - 1, top + height - 1), outline=0, width=5)
        bitmap = backend.prepare_bitmap(page, cfg)
        ink = ImageOps.invert(bitmap.convert("L"))
        self.assertIsNotNone(ink.crop((0, 0, 554, 6)).getbbox())
        self.assertIsNotNone(ink.crop((0, 893, 554, 899)).getbbox())
        self.assertIsNotNone(ink.crop((0, 0, 8, 899)).getbbox())
        self.assertIsNotNone(ink.crop((546, 0, 554, 899)).getbbox())
        self.assertEqual(bitmap.getpixel((277, 450)), 255)

    def test_wider_roll_preserves_content_size_and_centers_it(self):
        page = page_for_crop(config(), fill=0)
        narrow = backend.prepare_bitmap(page, config())
        wide = backend.prepare_bitmap(page, config(media_width_mm=62))
        self.assertEqual(narrow.tobytes(), wide.tobytes())
        preview = backend.physical_preview(wide, config(media_width_mm=62))
        self.assertEqual(preview.size, (732, 969))
        self.assertEqual(preview.crop((89, 35, 643, 934)).tobytes(), wide.tobytes())
        self.assertIsNone(ImageOps.invert(preview.convert("L")).crop((0, 0, 89, 969)).getbbox())

    def test_crop_must_fit_rendered_page(self):
        with self.assertRaises(backend.RenderError):
            backend.crop_page(Image.new("L", (100, 100)), config())


class GhostscriptTests(unittest.TestCase):
    PS = b"""%!PS-Adobe-3.0
%%Pages: 2
%%Page: 1 1
/Helvetica findfont 24 scalefont setfont 72 720 moveto (one) show
showpage
%%Page: 2 2
/Helvetica findfont 24 scalefont setfont 72 720 moveto (two) show
showpage
%%EOF
"""

    @unittest.skipUnless(GS_WORKS, "Ghostscript is not executable on this host")
    def test_real_ghostscript_renders_every_ps_and_pdf_page(self):
        cfg = config()
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            pdf_path = directory / "input.pdf"
            subprocess.run(
                [
                    "gs",
                    "-q",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-sDEVICE=pdfwrite",
                    f"-sOutputFile={pdf_path}",
                    "-",
                ],
                input=self.PS,
                check=True,
            )
            for name, document in (("ps", self.PS), ("pdf", pdf_path.read_bytes())):
                render_dir = directory / name
                render_dir.mkdir()
                with self.subTest(format=name):
                    pages = backend.render_pages(document, cfg, render_dir)
                    self.assertEqual(len(pages), 2)
                    for page_path in pages:
                        with Image.open(page_path) as page:
                            self.assertGreater(page.width, 2000)


class RasterTests(unittest.TestCase):
    def test_real_brother_ql_raster_contains_media_margin_and_cut_commands(self):
        height = 899
        payload = backend.raster_instructions(Image.new("1", (554, height), 1))
        media = payload.index(b"\x1b\x69\x7a")
        self.assertEqual(payload[media + 4 : media + 7], bytes((0x0A, 50, 0)))
        self.assertEqual(payload[media + 7 : media + 11], struct.pack("<L", height))
        self.assertIn(b"\x1b\x69\x4d\x40", payload)
        self.assertIn(b"\x1b\x69\x41\x01", payload)
        self.assertIn(b"\x1b\x69\x4b\x08", payload)
        self.assertIn(b"\x1b\x69\x64\x23\x00", payload)
        self.assertTrue(payload.endswith(b"\x1a"))

    def test_white_background_has_no_ink_and_black_pixel_is_positioned(self):
        white = Image.new("1", (554, 899), 1)
        blank = backend.raster_instructions(white)
        raster_start = blank.index(b"\x67\x00\x5a")
        rows = blank[raster_start:-1]
        self.assertEqual(rows, (b"\x67\x00\x5a" + bytes(90)) * 899)
        white.putpixel((0, 0), 0)
        marked = backend.raster_instructions(white)
        first_row = marked[raster_start + 3:raster_start + 93]
        bits = ''.join(f'{byte:08b}' for byte in first_row)
        self.assertEqual(bits.count('1'), 1)
        # QL raster transmission mirrors the 720-dot row: left image pixel at 553+12.
        self.assertEqual(bits.index('1'), 565)

    def test_62mm_media_command_and_print_head_position(self):
        bitmap = Image.new("1", (554, 899), 1)
        bitmap.putpixel((0, 0), 0)
        payload = backend.raster_instructions(bitmap, 62)
        media = payload.index(b"\x1b\x69\x7a")
        self.assertEqual(payload[media + 4:media + 7], bytes((0x0A, 62, 0)))
        start = payload.index(b"\x67\x00\x5a") + 3
        bits = ''.join(f'{value:08b}' for value in payload[start:start + 90])
        self.assertEqual(bits.count('1'), 1)
        self.assertEqual(bits.index('1'), 636)  # mirror of x=12+71 on the 720-dot head
        self.assertIn(b"\x1b\x69\x41\x01", payload)
        self.assertTrue(payload.endswith(b"\x1a"))

    def test_unconfirmed_or_error_usb_status_is_never_success(self):
        statuses = [
            {"outcome": "sent", "did_print": False, "ready_for_next_job": False},
            {"outcome": "error", "did_print": False, "ready_for_next_job": True},
            {"outcome": "printed", "did_print": True, "ready_for_next_job": False},
        ]
        for status in statuses:
            with self.subTest(status=status):
                with mock.patch("brother_ql.backends.helpers.send", return_value=status):
                    with self.assertRaises(backend.UnconfirmedPrintError):
                        backend.send_label(b"instructions", object())
        success = {"outcome": "printed", "did_print": True, "ready_for_next_job": True}
        with mock.patch("brother_ql.backends.helpers.send", return_value=success):
            backend.send_label(b"instructions", object())

    def test_unconfirmed_print_maps_to_cups_hold(self):
        with mock.patch.object(
            backend, "cups_print", side_effect=backend.UnconfirmedPrintError("unbestätigt")
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                result = backend.main(["1", "user", "title", "1", ""])
        self.assertEqual(result, backend.CUPS_BACKEND_HOLD)


class UsbDiscoveryTests(unittest.TestCase):
    class Device:
        def __init__(self, vendor, product, serial):
            self.idVendor = vendor
            self.idProduct = product
            self.serial_number = serial

    def test_discovery_filters_exact_model_and_emits_serial_uri(self):
        right = self.Device(0x04F9, 0x209B, "QL8SERIAL")
        other_brother = self.Device(0x04F9, 0x2042, "WRONG")
        not_brother = self.Device(0x1234, 0x209B, "WRONG2")
        available = [right, other_brother, not_brother]
        with mock.patch("usb.core.find", return_value=available) as find:
            self.assertEqual(
                backend.list_printer_uris(), ["usb://0x04f9:0x209b/QL8SERIAL"]
            )
            find.assert_called_once_with(find_all=True, idVendor=0x04F9, idProduct=0x209B)

    def test_serial_resolution_and_ambiguous_unqualified_uri(self):
        first = self.Device(0x04F9, 0x209B, "ONE")
        second = self.Device(0x04F9, 0x209B, "TWO")
        devices = [
            ("usb://0x04f9:0x209b/ONE", first),
            ("usb://0x04f9:0x209b/TWO", second),
        ]
        with mock.patch.object(backend, "discover_devices", return_value=devices):
            self.assertIs(backend.resolve_device("usb://0x04f9:0x209b/TWO"), second)
            with self.assertRaises(backend.PrinterError):
                backend.resolve_device("usb://0x04f9:0x209b")


class PrinterStatusTests(unittest.TestCase):
    def test_connected_ql800_media_code_is_accepted_without_skipping_checks(self):
        for code in (0x0A, 0x4A):
            with self.subTest(code=code):
                status = status_reply(width=62, kind=code)
                self.assertEqual(status["media_type"], "Endlosrolle")
                self.assertEqual(status["media_type_code"], code)
                backend.check_media(status, 62)
                with self.assertRaisesRegex(backend.PrinterError, "erkennt 62 mm"):
                    backend.check_media(status, 50)
                with self.assertRaisesRegex(backend.PrinterError, "Abdeckung"):
                    backend.check_media(status_reply(width=62, kind=code, error2=16), 62)
                with self.assertRaises(backend.PrinterError):
                    backend.check_media(status_reply(width=62, kind=code, length=86), 62)
        for code in (0, 0x0B, 0x4B, 0x8A, 0xFF):
            with self.subTest(rejected_code=code):
                with self.assertRaises(backend.PrinterError):
                    backend.check_media(status_reply(width=62, kind=code), 62)

    def test_decode_endless_and_die_cut_are_distinct(self):
        self.assertEqual(status_reply(width=62)["media_type"], "Endlosrolle")
        cut = status_reply(width=60, kind=0x4B, length=86)
        self.assertEqual(cut["media_length_mm"], 86)
        with self.assertRaisesRegex(backend.PrinterError, "Einzeletiketten"):
            backend.check_media(cut, 62)
        backend.check_media(status_reply(width=62), 62)

    def test_media_mismatch_and_errors_are_actionable(self):
        with self.assertRaisesRegex(backend.PrinterError, "erkennt 62 mm, eingestellt sind 50"):
            backend.check_media(status_reply(width=62), 50)
        for error1, error2, message in ((4, 0, "Schneideeinheit"), (0, 16, "Abdeckung"), (0, 4, "Kommunikationsfehler")):
            with self.subTest(error=(error1, error2)):
                with self.assertRaisesRegex(backend.PrinterError, message):
                    backend.check_media(status_reply(error1=error1, error2=error2), 50)

    def test_status_query_sends_only_query_and_waits_for_fresh_reply(self):
        port = mock.Mock()
        port.read_dev.read.side_effect = [raw_status(status_type=1), raw_status(width=62)]
        with mock.patch("brother_ql.backends.pyusb.BrotherQLBackendPyUSB", return_value=port):
            result = backend.query_printer_status(object())
        self.assertEqual(result["media_width_mm"], 62)
        port.write.assert_called_once_with(b"\x1b\x69\x53")
        port.dispose.assert_called_once()

    def test_status_timeout_and_bad_frame_fail_closed(self):
        from usb.core import USBTimeoutError
        port = mock.Mock()
        port.read_dev.read.side_effect = USBTimeoutError('timeout')
        with mock.patch("brother_ql.backends.pyusb.BrotherQLBackendPyUSB", return_value=port):
            with mock.patch.object(backend.time, "monotonic", side_effect=[0, 0, 4]):
                with self.assertRaisesRegex(backend.PrinterError, "Keine aktuelle"):
                    backend.query_printer_status(object())
        port.dispose.assert_called_once()
        for invalid in (b"", bytes(32), raw_status()[:31]):
            with self.assertRaises(backend.PrinterError):
                backend.decode_printer_status(invalid)


class PowerOffTests(unittest.TestCase):
    def run_transaction(self, replies, minutes):
        port = mock.Mock()
        port.read_dev.read.side_effect = replies
        patcher = mock.patch(
            "brother_ql.backends.pyusb.BrotherQLBackendPyUSB", return_value=port
        )
        with patcher:
            result = backend._power_off_transaction(object(), minutes)
        return result, port

    def test_changes_60_to_off_with_exact_commands_and_readback(self):
        result, port = self.run_transaction(
            [raw_status(), raw_power(6), raw_power(0), raw_status()],
            0,
        )
        self.assertEqual(
            result,
            {
                "auto_power_off_minutes": 0,
                "automatic_shutdown_disabled": True,
                "previous_auto_power_off_minutes": 60,
                "changed": True,
            },
        )
        self.assertEqual(
            [call.args[0] for call in port.write.call_args_list],
            [
                b"\x1b\x69\x53",
                b"\x1b\x69\x61\x01",
                b"\x1b\x69\x55\x41\x01",
                b"\x1b\x69\x55\x41\x00\x00",
                b"\x1b\x69\x55\x41\x01",
                b"\x1b\x69\x53",
            ],
        )
        port.dispose.assert_called_once_with()

    def test_changes_off_to_20_and_discards_normal_notifications(self):
        result, port = self.run_transaction(
            [
                raw_status(status_type=1),
                raw_status(width=0, kind=0),
                raw_status(status_type=2),
                raw_power(0),
                raw_power(2),
                raw_status(width=0, kind=0),
            ],
            20,
        )
        self.assertEqual(result["previous_auto_power_off_minutes"], 0)
        self.assertEqual(result["auto_power_off_minutes"], 20)
        self.assertFalse(result["automatic_shutdown_disabled"])
        self.assertTrue(result["changed"])
        self.assertNotIn(b"\x1a", [call.args[0] for call in port.write.call_args_list])
        port.dispose.assert_called_once_with()

    def test_unchanged_value_avoids_eeprom_write(self):
        result, port = self.run_transaction([raw_status(), raw_power(2)], 20)
        self.assertEqual(result["previous_auto_power_off_minutes"], 20)
        self.assertFalse(result["changed"])
        self.assertEqual(
            [call.args[0] for call in port.write.call_args_list],
            [backend.STATUS_REQUEST, backend.RASTER_MODE, backend.GET_AUTO_POWER_OFF],
        )
        port.dispose.assert_called_once_with()

    def test_status_action_returns_only_public_power_fields(self):
        result, port = self.run_transaction([raw_status(), raw_power(4)], None)
        self.assertEqual(
            result,
            {
                "auto_power_off_minutes": 40,
                "automatic_shutdown_disabled": False,
            },
        )
        port.dispose.assert_called_once_with()

    def test_cli_zero_is_a_setting_action(self):
        with mock.patch.object(
            backend, "show_or_set_auto_power_off", return_value=0
        ) as action:
            self.assertEqual(backend.main(["--set-auto-power-off", "0"]), 0)
        action.assert_called_once_with(backend.DEFAULT_CONFIG, 0)

    def test_invalid_cli_minutes_are_rejected_before_usb(self):
        with mock.patch.object(
            backend, "show_or_set_auto_power_off", side_effect=AssertionError
        ) as action:
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as raised:
                    backend.main(["--set-auto-power-off", "15"])
        self.assertEqual(raised.exception.code, 2)
        action.assert_not_called()

    def test_busy_lock_fails_before_device_resolution_or_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(backend, "LOCK_PATH", str(Path(directory) / "lock")),
                mock.patch.object(backend, "load_config", return_value=config()),
                mock.patch("fcntl.flock", side_effect=BlockingIOError),
                mock.patch.object(backend, "resolve_device") as resolve,
                mock.patch.object(backend, "_power_off_transaction") as transact,
            ):
                with self.assertRaisesRegex(backend.PrinterError, "anderen Auftrag"):
                    backend.show_or_set_auto_power_off("config", None)
        resolve.assert_not_called()
        transact.assert_not_called()

    def test_bad_header_timeout_ack_and_out_of_range_fail_closed(self):
        cases = [
            (bytes(32), None, "Ungültige Statusantwort"),
            (raw_power(2, ack=0), None, "nicht bestätigt"),
            (raw_power(7), None, "Ungültiger Wert"),
        ]
        for reply, minutes, message in cases:
            with self.subTest(message=message):
                port = mock.Mock()
                port.read_dev.read.side_effect = [raw_status(), reply]
                with mock.patch(
                    "brother_ql.backends.pyusb.BrotherQLBackendPyUSB",
                    return_value=port,
                ):
                    with self.assertRaisesRegex(backend.PrinterError, message):
                        backend._power_off_transaction(object(), minutes)
                port.dispose.assert_called_once_with()

        from usb.core import USBTimeoutError

        port = mock.Mock()
        port.read_dev.read.side_effect = USBTimeoutError("private usb detail")
        with mock.patch(
            "brother_ql.backends.pyusb.BrotherQLBackendPyUSB", return_value=port
        ):
            with mock.patch.object(backend.time, "monotonic", side_effect=[0, 0, 4]):
                with self.assertRaisesRegex(backend.PrinterError, "Keine aktuelle"):
                    backend._power_off_transaction(object(), None)
        port.dispose.assert_called_once_with()

    def test_mismatch_and_reported_printer_error_cleanup(self):
        replies = [raw_status(), raw_power(0), raw_power(3)]
        port = mock.Mock()
        port.read_dev.read.side_effect = replies
        with mock.patch(
            "brother_ql.backends.pyusb.BrotherQLBackendPyUSB", return_value=port
        ):
            with self.assertRaisesRegex(backend.PrinterError, "konnte nicht bestätigt"):
                backend._power_off_transaction(object(), 20)
        port.dispose.assert_called_once_with()

        port = mock.Mock()
        port.read_dev.read.side_effect = [raw_status(error2=0x10)]
        with mock.patch(
            "brother_ql.backends.pyusb.BrotherQLBackendPyUSB", return_value=port
        ):
            with self.assertRaisesRegex(backend.PrinterError, "Abdeckung"):
                backend._power_off_transaction(object(), None)
        port.dispose.assert_called_once_with()

    def test_unlocks_and_disposes_when_usb_write_raises(self):
        port = mock.Mock()
        port.write.side_effect = RuntimeError("private usb detail")
        with tempfile.TemporaryDirectory() as directory:
            lock_calls = []

            def flock(_descriptor, operation):
                lock_calls.append(operation)

            with (
                mock.patch.object(backend, "LOCK_PATH", str(Path(directory) / "lock")),
                mock.patch.object(backend, "load_config", return_value=config()),
                mock.patch.object(backend, "resolve_device", return_value=object()),
                mock.patch(
                    "brother_ql.backends.pyusb.BrotherQLBackendPyUSB",
                    return_value=port,
                ),
                mock.patch("fcntl.flock", side_effect=flock),
            ):
                with self.assertRaisesRegex(backend.PrinterError, "USB-Energieeinstellung"):
                    backend.show_or_set_auto_power_off("config", None)
        port.dispose.assert_called_once_with()
        self.assertEqual(
            lock_calls,
            [backend.fcntl.LOCK_EX | backend.fcntl.LOCK_NB, backend.fcntl.LOCK_UN],
        )


class CupsFlowTests(unittest.TestCase):
    def test_all_pages_are_prepared_then_each_copy_and_page_is_sent(self):
        events = []
        images = [Image.new("1", (554, 899), 1), Image.new("1", (554, 899), 0)]

        def raster(image, media_width):
            self.assertEqual(media_width, 50)
            events.append("raster")
            return b"white" if image.getpixel((0, 0)) else b"black"

        def send(payload, device):
            events.append(payload.decode("ascii"))

        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / "usb.lock")
            with (
                mock.patch.object(backend, "LOCK_PATH", lock_path),
                mock.patch.object(backend, "load_config", return_value=config()),
                mock.patch.object(backend, "read_input", return_value=b"%PDF-1.4\n%%EOF"),
                mock.patch.object(backend, "process_document", return_value=images),
                mock.patch.object(backend, "raster_instructions", side_effect=raster),
                mock.patch.object(backend, "resolve_device", return_value=object()),
                mock.patch.object(backend, "send_label", side_effect=send),
                mock.patch.object(backend, "query_printer_status", return_value=status_reply()),
                mock.patch.dict("os.environ", {"DEVICE_URI": "formularkopf-klebchen://ql800"}),
            ):
                result = backend.cups_print(
                    ["42", "user", "secret title", "3", "", "/spooled/job"]
                )

        self.assertEqual(result, backend.CUPS_BACKEND_OK)
        self.assertEqual(
            events,
            ["raster", "raster", "white", "black", "white", "black", "white", "black"],
        )

    def test_media_mismatch_prevents_any_print_data_being_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(backend, "LOCK_PATH", str(Path(directory) / 'lock')),
                mock.patch.object(backend, "load_config", return_value=config()),
                mock.patch.object(backend, "read_input", return_value=b"%PDF-1.4"),
                mock.patch.object(backend, "process_document", return_value=[Image.new('1', (554, 899))]),
                mock.patch.object(backend, "resolve_device", return_value=object()),
                mock.patch.object(backend, "query_printer_status", return_value=status_reply(width=62)),
                mock.patch.object(backend, "send_label") as send,
                mock.patch.dict("os.environ", {"DEVICE_URI": "formularkopf-klebchen://ql800"}),
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                self.assertEqual(backend.main(['1', 'user', 'private title', '1', '']), 3)
                send.assert_not_called()
                self.assertIn('erkennt 62 mm', stderr.getvalue())
                self.assertNotIn('private title', stderr.getvalue())

    def test_wrong_cups_device_uri_is_rejected(self):
        with mock.patch.dict("os.environ", {"DEVICE_URI": "usb://wrong"}):
            with self.assertRaises(backend.ConfigurationError):
                backend.cups_print(["1", "user", "title", "1", ""])

    def test_preview_uses_no_usb_or_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            with (
                mock.patch.object(backend, "load_config", return_value=config()),
                mock.patch.object(backend, "read_input", return_value=b"%PDF-1.4\n%%EOF"),
                mock.patch.object(
                    backend,
                    "process_document",
                    return_value=[Image.new("1", (554, 899), 1)],
                ),
                mock.patch.object(backend, "resolve_device", side_effect=AssertionError),
                mock.patch("fcntl.flock", side_effect=AssertionError),
            ):
                self.assertEqual(backend.preview("config", "input", str(output)), 0)
            with Image.open(output / "page-001.png") as preview:
                self.assertEqual(preview.size, (590, 969))


if __name__ == "__main__":
    unittest.main()
