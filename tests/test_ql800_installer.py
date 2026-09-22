"""Exercise the real installer dialog without packages or system changes."""
import os
from pathlib import Path
import pty
import subprocess
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "ql800" / "installer.sh"
SOURCE = INSTALLER.read_text()
DIALOG = SOURCE.split('if [[ -z "$AUTO_POWER_OFF" ]]; then\n', 1)[1]
DIALOG = 'if [[ -z "$AUTO_POWER_OFF" ]]; then\n' + DIALOG.split('\nHAD_SAMBA_CONFIG=0\n', 1)[0]
APPLY = SOURCE.split('if [[ "$AUTO_POWER_OFF" != keep ]]; then\n', 1)[1]
APPLY = 'if [[ "$AUTO_POWER_OFF" != keep ]]; then\n' + APPLY.split("\necho '=== 5/6", 1)[0]


def dialog(value="", answer=None):
    script = '''set -eu
AUTO_POWER_OFF=$1
fail() { echo "$*" >&2; exit 1; }
''' + DIALOG + '\nprintf "RESULT=%s\\n" "$AUTO_POWER_OFF"\n'
    args = ["bash", "-c", script, "installer-dialog", value]
    if answer is None:
        return subprocess.run(args, input="", capture_output=True, text=True, timeout=5)
    master, slave = pty.openpty()
    try:
        with subprocess.Popen(args, stdin=slave, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            os.write(master, answer.encode())
            stdout, stderr = process.communicate(timeout=5)
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    finally:
        os.close(master)
        os.close(slave)


class PowerDialogTests(unittest.TestCase):
    def test_enter_and_unattended_install_default_to_off(self):
        for answer in (None, "\n"):
            with self.subTest(answer=answer):
                result = dialog(answer=answer)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("RESULT=off", result.stdout)
        self.assertIn("[aus]", dialog(answer="\n").stderr)

    def test_interactive_choices_and_invalid_retry(self):
        for answer, expected in (("beibehalten\n", "keep"), ("AUS\n", "off"),
                                 ("30\n", "30"), ("17\n60\n", "60")):
            with self.subTest(answer=answer):
                result = dialog(answer=answer)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("RESULT=" + expected, result.stdout)
        self.assertIn("Bitte aus", dialog(answer="17\n60\n").stdout)

    def test_explicit_choices_skip_question(self):
        for value in ("off", "keep", "10", "20", "30", "40", "50", "60"):
            with self.subTest(value=value):
                result = dialog(value=value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "RESULT=" + value)
                self.assertEqual(result.stderr, "")

    def test_cli_rejects_invalid_setting_before_installing(self):
        result = subprocess.run(["bash", str(INSTALLER), "--auto-power-off", "17"],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--auto-power-off erwartet", result.stderr)
        self.assertNotIn("Pakete", result.stdout)

    def test_apply_maps_off_to_zero_and_keep_does_not_access_usb(self):
        script = '''set -eu
AUTO_POWER_OFF=$1
VENV=/test/venv
LIBDIR=/test/lib
runuser() { printf 'CALL=%s\\n' "$*"; return "${TEST_STATUS:-0}"; }
''' + APPLY + '\nprintf "FINISHED\\n"\n'
        for value, minutes in (("off", "0"), ("30", "30"), ("keep", None)):
            with self.subTest(value=value):
                result = subprocess.run(["bash", "-c", script, "installer-apply", value],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                if minutes is None:
                    self.assertNotIn("CALL=", result.stdout)
                else:
                    self.assertIn("--set-auto-power-off " + minutes, result.stdout)
                    self.assertIn("CALL=-u lp --", result.stdout)
                self.assertIn("FINISHED", result.stdout)
        result = subprocess.run(["bash", "-c", "TEST_STATUS=1\n" + script,
                                 "installer-apply", "off"], capture_output=True,
                                text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("FINISHED", result.stdout)


if __name__ == "__main__":
    unittest.main()
