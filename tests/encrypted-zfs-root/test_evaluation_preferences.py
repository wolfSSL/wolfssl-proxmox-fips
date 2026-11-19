"""Run the actual guide pinning block on public metadata fixtures, without real DEBs."""
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / 'docs/_foundation/WOLFSSL-PROXMOX-FIPS-ENCRYPTED-ZFS-ROOT.md'
TEXT = GUIDE.read_text()
BUNDLE_BLOCK = next(block for block in re.findall(r'```sh\n(.*?)\n```', TEXT, re.S)
                    if 'bundle=/root/<evaluation-bundle>' in block)
HEADER = BUNDLE_BLOCK.split('bundle=', 1)[0]
BLOCK = TEXT.split('# Keep these authenticated revisions ahead of the public repository preferences.\n', 1)[1].split('selected=("${evaluation_packages[@]}"', 1)[0]
NAMES = [
    'proxmox-kernel-7.0.14-23+wolfcrng11-pve', 'wolfssl-fips-kmod-7.0.14-23',
    'wolfzfs-kmod-7.0.14-23', 'proxmox-kernel-wolfcrng', 'wolfzfs-kmod',
    'wolfssl-fips-pve-config', 'wolfssl-fips-pve-zfs-root',
    'libpve-common-perl', 'libpve-access-control', 'libpve-rs-perl',
]
VERSIONS = ['1.0-1', '1.0-2', '1.0-3', '1.0-4', '1.0-5',
            '1.0.28-1~ezr20261119.1', '0.1.0-1~ezr20261118.5',
            '9.2.11+wolfauth1', '9.2.2+wolfauth1', '0.16.2+wolfmd53']
OLD = b'PUBLIC_PREVIOUS_EVALUATION_PREFERENCES\n'
GENERAL = b'Package: *\nPin: origin packages.wolfssl.com\nPin-Priority: 1001\n'


class EvaluationPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='ezr-public-preferences-')
        self.root = Path(self.directory.name)
        self.preferences = self.root / 'preferences'
        self.preferences.mkdir()
        self.final = self.preferences / '99-wolfssl-encrypted-root-evaluation'
        self.final.write_bytes(OLD)
        self.general = self.preferences / 'public-general'
        self.general.write_bytes(GENERAL)
        self.rows = [{'Package': name, 'Version': version}
                     for name, version in zip(NAMES, VERSIONS)]
        self.inputs = [self.root / f'PUBLIC-package-{n}.deb' for n in range(10)]

    def tearDown(self):
        self.directory.cleanup()

    def run_block(self, rows=None, count=10):
        fixture = self.root / 'public-metadata.json'
        fixture.write_text(json.dumps(dict(zip(map(str, self.inputs), rows or self.rows))))
        query = self.root / 'public-dpkg-deb'
        query.write_text('''#!/usr/bin/python3
import json,sys
from pathlib import Path
assert len(sys.argv)==4 and sys.argv[1]=='--field'
assert sys.argv[3] in ('Package','Version')
row=json.loads(Path(__file__).with_name('public-metadata.json').read_text())[sys.argv[2]]
if row.get('failure')==sys.argv[3]: raise SystemExit(7)
print(row.get(sys.argv[3],''))
''')
        query.chmod(0o700)
        body = BLOCK.replace('/etc/apt/preferences.d/', str(self.preferences) + '/')
        body = body.replace('/usr/bin/dpkg-deb', shlex.quote(str(query)))
        # Take strict-shell setup from the actual reusable guide fence, not the test harness.
        script = HEADER + 'evaluation_packages=(' + ' '.join(map(shlex.quote, map(str, self.inputs[:count]))) + ')\n' + body
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('SHELLOPTS', 'BASHOPTS', 'BASH_ENV')}
        result = subprocess.run(['bash', '--noprofile', '--norc'], input=script,
                                capture_output=True, text=True, env=environment)
        self.assertEqual(self.general.read_bytes(), GENERAL)
        return result

    def expect_old_preserved(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.final.read_bytes(), OLD)

    def test_exact_ten_stanzas_priority1002_and_atomic_replacement(self):
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = ''.join(f'Package: {name}\nPin: version {version}\nPin-Priority: 1002\n\n'
                           for name, version in zip(NAMES, VERSIONS))
        self.assertEqual(self.final.read_text(), expected)
        self.assertEqual(stat.S_IMODE(self.final.stat().st_mode), 0o644)
        self.assertEqual(list(self.preferences.glob('.wolfssl-encrypted-root-evaluation.*')), [])

    def test_metadata_failure_keeps_prior_file_and_hidden_partial(self):
        self.rows[7]['failure'] = 'Version'
        self.expect_old_preserved(self.run_block())
        partials = list(self.preferences.glob('.wolfssl-encrypted-root-evaluation.*'))
        self.assertEqual(len(partials), 1)
        self.assertEqual(stat.S_IMODE(partials[0].stat().st_mode), 0o600)
        self.assertNotIn('libpve-common-perl', partials[0].read_text())

    def test_last_query_failure_never_promotes_partial_preferences(self):
        self.rows[9]['failure'] = 'Version'
        self.expect_old_preserved(self.run_block())

    def test_fresh_shell_uses_actual_fence_errexit_before_metadata_failure(self):
        self.assertTrue(BUNDLE_BLOCK.startswith('set -euo pipefail\n'))
        self.rows[7]['failure'] = 'Package'
        self.expect_old_preserved(self.run_block())

    def test_wildcard_package_name_refused(self):
        self.rows[7]['Package'] = '*'
        self.expect_old_preserved(self.run_block())

    def test_injected_version_field_refused(self):
        self.rows[7]['Version'] = '9.2.11\nPin-Priority: -1'
        self.expect_old_preserved(self.run_block())

    def test_duplicate_package_metadata_refused(self):
        self.rows[8]['Package'] = NAMES[7]
        self.expect_old_preserved(self.run_block())

    def test_missing_version_refused(self):
        self.rows[7].pop('Version')
        self.expect_old_preserved(self.run_block())

    def test_nine_packages_refused(self):
        self.expect_old_preserved(self.run_block(count=9))

    def test_existing_symlink_refused_before_creating_temporary_file(self):
        target = self.root / 'PUBLIC_SYMLINK_TARGET'
        target.write_bytes(OLD)
        self.final.unlink()
        self.final.symlink_to(target)
        result = self.run_block()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.final.is_symlink())
        self.assertEqual(target.read_bytes(), OLD)
        self.assertEqual(list(self.preferences.glob('.wolfssl-encrypted-root-evaluation.*')), [])

    def test_existing_directory_refused_before_creating_temporary_file(self):
        self.final.unlink()
        self.final.mkdir()
        result = self.run_block()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.final.is_dir())
        self.assertEqual(list(self.preferences.glob('.wolfssl-encrypted-root-evaluation.*')), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
