"""Public synthetic package fixtures and retained public manifest text; no DEB build."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'docs/_foundation/scripts/verify-evaluation-selection.py'
spec = importlib.util.spec_from_file_location('public_evaluation_selection', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# Retained public metadata only; this does not authenticate or assemble a current bundle.
HISTORICAL_MANIFEST = """5480381e384bc2103a9e61b8bdb570bc0fe00bf988ea345145f4364995711609  EVALUATION-BUNDLE.json
524321b3afcc3e1798f778bdddb2b684089e721bdf5ee02c717d30dc3f04c6e2  PROVENANCE.json
b469c233b5f9f8c04b66aadf342753d26c86c1baffe5aea982b29fd20c0dacf4  auth/COMMON2-QUALIFICATION.json
12123871fab6f9c812670d122e950592f10e9991f37cd175cbf0d67376dcc5f8  auth/InRelease
dae3815d6f350552f67f1fd1c8165cb613e71ac7d1137ca63d2d437f518865ae  auth/PUBLIC-ARTIFACTS.json
b3986aa792b30f256405da5e88cebda58e7467d4b45465917cb6ad96ccc19cde  auth/ROOT5-QUALIFICATION.json
27ddc7b3cedfc05b95c9ec58b67dea34ebaaacffba31ff52bdf176be549dd693  auth/Release
8f7c44ca9621cc5fc7e774de48c0cc6d116c521bff17b9e7c6d705009838d48b  auth/archive-key.gpg
c1ec2e60eaaa335d2d46a87af9c64a2072426ed74d48cc088e4ad7173ce6bd7e  auth/kernel-signature.log
5125639ddbaabdbf2ed76943e5dd76d8bd18cd1e5c2343bbb7b8f764b8a46707  auth/main-Packages.gz
4bfb7bbef3dedf52107708a2daeeedd263ac2f60f51e832c553117a0d808b91f  auth/reused-metadata-provenance.json
24a211eb1680159153559f637897123cb102449bf71b81644f835fc1249559d9  proxmox-kernel-7.0.14-23+wolfcrng11-pve_7.0.14-23_amd64.deb
b588ee3d3160ee490fe475c7ea7f8f5a5766b727b26436db6e55e308f526ee89  proxmox-kernel-wolfcrng_7.0.14-23+wolfcrng11+zfsroot20261118.2_amd64.deb
594906edb874ecddf1dd61bef51878438e534e93c7ed7c5253a078dd64f78015  verify-bundle.py
31b60b0b70ad0132c99074a3ddf6a32f12ef9ecc66ed7d0e0aef78a6daeee575  wolfssl-fips-kmod-7.0.14-23+wolfcrng11-pve_5.9.1-1+fipsready1+zfsroot20261118.2_amd64.deb
0008ec2c74735b00f8bfcb71d913ed6a8542a64ed58cb523981de311e9590c84  wolfssl-fips-pve-config_1.0.28-1~ezr20261118.2_all.deb
6214efe5c7bbe736e82b9ae4f2257160edde95c342a4e0baa30ba863f6c3ca62  wolfssl-fips-pve-zfs-root_0.1.0-1~ezr20261118.5_amd64.deb
c0230d26387be8842c64d9a6e7d486b60050d16dbb98b55a986913c55ca35edd  wolfzfs-kmod-7.0.14-23+wolfcrng11-pve_2.4.4-1+wolfzfs3+zfsroot20261118.2_amd64.deb
276a7dac9a3a65088cd162f5bc6d82964c6184e026e89c7a32c28ba5fa869526  wolfzfs-kmod_7.0.14-23+wolfcrng11+zfsroot20261118.2_amd64.deb
"""
HISTORICAL_PACKAGE_NAMES = [
    'proxmox-kernel-7.0.14-23+wolfcrng11-pve_7.0.14-23_amd64.deb',
    'proxmox-kernel-wolfcrng_7.0.14-23+wolfcrng11+zfsroot20261118.2_amd64.deb',
    'wolfssl-fips-kmod-7.0.14-23+wolfcrng11-pve_5.9.1-1+fipsready1+zfsroot20261118.2_amd64.deb',
    'wolfssl-fips-pve-config_1.0.28-1~ezr20261118.2_all.deb',
    'wolfssl-fips-pve-zfs-root_0.1.0-1~ezr20261118.5_amd64.deb',
    'wolfzfs-kmod-7.0.14-23+wolfcrng11-pve_2.4.4-1+wolfzfs3+zfsroot20261118.2_amd64.deb',
    'wolfzfs-kmod_7.0.14-23+wolfcrng11+zfsroot20261118.2_amd64.deb',
]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='public-evaluation-selection-')
        self.root = Path(self.directory.name).resolve()
        self.selected = []
        self.rows = []
        names = [
            'proxmox-kernel-7.0.14-23+wolfcrng11-pve_1_amd64.deb',
            'wolfssl-fips-kmod-7.0.14-23_1_amd64.deb',
            'wolfzfs-kmod-7.0.14-23_1_amd64.deb',
            'proxmox-kernel-wolfcrng_1_amd64.deb',
            'wolfzfs-kmod_1_amd64.deb',
            'wolfssl-fips-pve-config_1_all.deb',
            'wolfssl-fips-pve-zfs-root_1_amd64.deb',
            'libpve-common-perl_1_all.deb',
            'libpve-access-control_1_all.deb',
            'libpve-rs-perl_1_amd64.deb',
        ]
        for index, name in enumerate(names):
            path = self.root / name
            data = ('PUBLIC_PACKAGE_FIXTURE_' + str(index) + '\n').encode()
            path.write_bytes(data)
            path.chmod(0o600)
            self.selected.append(path)
            self.rows.append(hashlib.sha256(data).hexdigest() + '  ' + path.name)
        auxiliary = self.root / 'auth'
        auxiliary.mkdir()
        (auxiliary / 'PUBLIC-PROVENANCE.json').write_text('{}\n')
        (auxiliary / 'PUBLIC-PROVENANCE.json').chmod(0o600)
        self.rows.append(hashlib.sha256(b'{}\n').hexdigest() + '  auth/PUBLIC-PROVENANCE.json')
        self.expected = self.write_rows(self.rows)

    def tearDown(self):
        self.directory.cleanup()

    def write_rows(self, rows):
        data = ('\n'.join(rows) + '\n').encode('ascii')
        (self.root / 'SHA256SUMS').write_bytes(data)
        (self.root / 'SHA256SUMS').chmod(0o600)
        return hashlib.sha256(data).hexdigest()

    def test_complete_ten_with_extra_public_provenance(self):
        result = m.verify(self.root, self.selected, self.expected)
        self.assertEqual(result['package_count'], 10)
        self.assertEqual(result['extra_public_rows'], 1)
        self.assertTrue(result['read_only'])

    def test_unlisted_seventh_refused_even_with_matching_manifest_digest(self):
        expected = self.write_rows(self.rows[:6] + self.rows[7:])
        with self.assertRaisesRegex(ValueError, 'absent'):
            m.verify(self.root, self.selected, expected)

    def test_duplicate_selected_manifest_path_refused(self):
        expected = self.write_rows(self.rows + [self.rows[0]])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            m.verify(self.root, self.selected, expected)

    def test_duplicate_auxiliary_path_refused(self):
        expected = self.write_rows(self.rows + [self.rows[-1]])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            m.verify(self.root, self.selected, expected)

    def test_traversal_absolute_and_noncanonical_paths_refused(self):
        for name in ('../PUBLIC-OUTSIDE.deb', str(self.root / 'PUBLIC-OUTSIDE.deb'),
                     'auth/../PUBLIC.deb', './PUBLIC.deb', 'auth//PUBLIC.json'):
            with self.subTest(name=name):
                expected = self.write_rows(self.rows + ['0' * 64 + '  ' + name])
                with self.assertRaisesRegex(ValueError, 'canonical relative'):
                    m.verify(self.root, self.selected, expected)

    def test_mismatched_independent_manifest_digest_refused(self):
        with self.assertRaisesRegex(ValueError, 'independently supplied'):
            m.verify(self.root, self.selected, '0' * 64)

    def test_mismatched_selected_bytes_refused(self):
        self.selected[-1].write_bytes(b'PUBLIC_CHANGED_PACKAGE\n')
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            m.verify(self.root, self.selected, self.expected)

    def test_symlink_package_refused_before_reading_target(self):
        target = self.root / 'PUBLIC-OTHER.txt'
        target.write_bytes(b'PUBLIC_ONLY\n')
        self.selected[-1].unlink()
        self.selected[-1].symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            m.verify(self.root, self.selected, self.expected)

    def test_duplicate_selected_argument_refused(self):
        with self.assertRaisesRegex(ValueError, 'distinct'):
            m.verify(self.root, [*self.selected[:9], self.selected[0]], self.expected)

    def test_selected_path_outside_bundle_refused(self):
        with self.assertRaisesRegex(ValueError, 'direct DEB member'):
            m.verify(self.root, [*self.selected[:9], self.root.parent / 'PUBLIC-OTHER.deb'], self.expected)

    def test_extra_unselected_deb_refused(self):
        (self.root / 'PUBLIC-extra_1_all.deb').write_bytes(b'PUBLIC_EXTRA\n')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            m.verify(self.root, self.selected, self.expected)

    def test_actual_cli_positive_against_public_fixture(self):
        result = subprocess.run([sys.executable, str(SOURCE), '--bundle', str(self.root),
                                 '--checksums-sha256', self.expected,
                                 *map(str, self.selected)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['package_count'], 10)

    def test_historical_seven_manifest_identity_metadata_only(self):
        data = HISTORICAL_MANIFEST.encode('ascii')
        self.assertEqual(hashlib.sha256(data).hexdigest(), 'ed546cdf081c676510f780efa8076703be02d550a71fdb7a85d485167509c5b4')
        rows = m.manifest_rows(data.decode('ascii'))
        package_names = HISTORICAL_PACKAGE_NAMES
        self.assertEqual(len(package_names), 7)
        self.assertTrue(all(name in rows for name in package_names))
        self.assertGreater(len(rows), 7)


    def test_historical_seven_selection_refused(self):
        with self.assertRaisesRegex(ValueError, 'exactly ten'):
            m.verify(self.root, self.selected[:7], self.expected)

    def test_each_unlisted_maintenance_package_refused(self):
        for index in (7, 8, 9):
            with self.subTest(package=self.selected[index].name):
                expected = self.write_rows(self.rows[:index] + self.rows[index + 1:])
                with self.assertRaisesRegex(ValueError, 'absent'):
                    m.verify(self.root, self.selected, expected)

    def test_duplicate_maintenance_selection_refused(self):
        with self.assertRaisesRegex(ValueError, 'distinct'):
            m.verify(self.root, [*self.selected[:9], self.selected[7]], self.expected)

    def test_extra_maintenance_revision_refused(self):
        (self.root / 'libpve-common-perl_2_all.deb').write_bytes(b'PUBLIC_EXTRA_MAINTENANCE_REVISION\n')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            m.verify(self.root, self.selected, self.expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
