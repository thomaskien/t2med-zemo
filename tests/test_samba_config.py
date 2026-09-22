import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'ql800/samba_config.py'
spec = importlib.util.spec_from_file_location('samba_config', SOURCE)
samba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samba)


class SambaTests(unittest.TestCase):
    def test_existing_globals_and_shares_preserved_and_idempotent(self):
        original = '[global]\n security = ads\n realm = PRAXIS.LOCAL\n include = /etc/samba/custom.conf\n\n[daten]\n path = /srv/daten\n valid users = @praxis\n'
        updated = samba.candidate(original)
        self.assertEqual(samba.candidate(updated), updated)
        self.assertTrue(updated.startswith(original.rstrip()))
        self.assertEqual(updated.count('[formularkopf-klebchen]'), 1)
        self.assertIn('guest ok = no', updated)
        self.assertIn('cups options = raw', updated)
        self.assertIn('use client driver = yes', updated)
        self.assertNotIn('map to guest', updated)

    def test_foreign_same_name_share_rejected(self):
        for header in ('[formularkopf-klebchen]', ' [FORMULARKOPF-KLEBCHEN]'):
            with self.assertRaisesRegex(ValueError, 'außerhalb'):
                samba.candidate('[global]\n' + header + '\n path = /important\n')

    def test_damaged_markers_rejected(self):
        for contents in (samba.BEGIN, samba.END, samba.BLOCK * 2, samba.END + '\n' + samba.BEGIN):
            with self.assertRaises(ValueError):
                samba.candidate(contents)

    def fake_testparm(self, path, parameter=None):
        if parameter == 'disable spoolss':
            return 'No'
        if parameter == 'server role':
            return 'standalone server'
        return Path(path).read_text()

    def test_configure_backs_up_and_updates_once(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'smb.conf'
            original = '[global]\n workgroup = PRAXIS\n[data]\n path = /srv/data\n'
            path.write_text(original)
            path.chmod(0o640)
            with patch.object(samba, 'testparm', side_effect=self.fake_testparm):
                backup = samba.configure(path)
                self.assertEqual(backup.read_text(), original)
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                self.assertIsNone(samba.configure(path))
            self.assertEqual(len(list(Path(td).glob('smb.conf.klebchen-backup-*'))), 1)
            self.assertFalse(list(Path(td).glob('.klebchen-*')))

    def test_invalid_candidate_leaves_live_file_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'smb.conf'
            original = '[global]\n workgroup = PRAXIS\n'
            path.write_text(original)
            def validate(candidate, parameter=None):
                if samba.BEGIN in Path(candidate).read_text():
                    raise ValueError('invalid')
                return self.fake_testparm(candidate, parameter)
            with patch.object(samba, 'testparm', side_effect=validate):
                with self.assertRaises(ValueError):
                    samba.configure(path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(td).iterdir()), [path])

    def test_external_include_share_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'smb.conf'
            original = '[global]\n include = external.conf\n'
            path.write_text(original)
            with patch.object(samba, 'testparm', return_value='[global]\n[formularkopf-klebchen]\n'):
                with self.assertRaisesRegex(ValueError, 'fremde Freigabe'):
                    samba.configure(path)
            self.assertEqual(path.read_text(), original)

    def test_new_configuration(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'smb.conf'
            with patch.object(samba, 'testparm', side_effect=self.fake_testparm):
                samba.configure(path)
            self.assertTrue(path.read_text().startswith(samba.BASE.rstrip()))
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_new_server_replaces_package_default_with_only_explicit_share(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'smb.conf'
            original = '[global]\n workgroup = WORKGROUP\n[printers]\n printable = yes\n'
            path.write_text(original)
            with patch.object(samba, 'testparm', side_effect=self.fake_testparm):
                backup = samba.configure(path, new_server=True)
            self.assertEqual(backup.read_text(), original)
            self.assertNotIn('[printers]', path.read_text())
            self.assertIn('load printers = no', path.read_text())

    def test_disabled_spoolss_and_dc_rejected(self):
        for parameter, result in [('disable spoolss', 'Yes'), ('server role', 'active directory domain controller')]:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / 'smb.conf'
                path.write_text('[global]\n')
                def validate(candidate, param=None):
                    return result if param == parameter else self.fake_testparm(candidate, param)
                with patch.object(samba, 'testparm', side_effect=validate):
                    with self.assertRaises(ValueError):
                        samba.configure(path)
                self.assertEqual(path.read_text(), '[global]\n')


if __name__ == '__main__':
    unittest.main()
