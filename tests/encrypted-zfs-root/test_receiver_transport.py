"""Offline public fixtures: no network, key generator, VM or operational key."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'docs/_foundation/scripts/receive-root-key.py'
spec = importlib.util.spec_from_file_location('public_receiver_transport', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
CANARY = b'PUBLIC_TRANSPORT_FIXTURE\x00\xff\n'
BOOT = '11111111-1111-4111-8111-111111111111'
CONTROLS = ('ControlMaster=no', 'ControlPath=none', 'ControlPersist=no',
            'IdentitiesOnly=yes', 'BatchMode=yes', 'StrictHostKeyChecking=yes',
            'HostKeyAlgorithms=ecdsa-sha2-nistp256',
            'PubkeyAcceptedAlgorithms=ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521')


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='public-receiver-fixture-')
        self.root = Path(self.directory.name).resolve()
        self.config = self.root / 'ssh.conf'
        self.config.write_text('Host public-fixture\n HostName public-fixture.invalid\n')
        self.config.chmod(0o600)
        self.destination = self.root / 'PUBLIC-TRANSPORT-OUTPUT'
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def run_receiver(self, mode='success', boot=BOOT, alias='public-fixture'):
        original_read = Path.read_text
        original_lstat = Path.lstat

        def public_guard_read(path, *args, **kwargs):
            fixtures = {'/proc/sys/kernel/core_pattern': 'core.disabled\n',
                        '/proc/sys/fs/suid_dumpable': '0\n',
                        '/proc/swaps': 'Filename Type Size Used Priority\n'}
            return fixtures[str(path)] if str(path) in fixtures else original_read(path, *args, **kwargs)

        def fixture_root_owner(path, *args, **kwargs):
            actual = original_lstat(path, *args, **kwargs)
            if path in (self.config, self.root):
                return types.SimpleNamespace(st_mode=actual.st_mode, st_uid=0)
            return actual

        def fake_ssh(argv, **kwargs):
            self.calls.append((argv, kwargs))
            for value in CONTROLS:
                self.assertEqual(argv.count(value), 1)
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            if argv[-1] == 'wolfssl-fips-zfs-root-prepare --check':
                self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
                return types.SimpleNamespace(returncode=0)
            remote = shlex.split(argv[-1])
            self.assertEqual(remote[:2], ['/usr/bin/python3', '-c'])
            self.assertIn(repr(BOOT), remote[2])
            self.assertIn("os.environ['OPENSSL_CONF'] = '/etc/wolfprovider/openssl.cnf'", remote[2])
            self.assertIn("os.execv('/usr/sbin/wolfssl-fips-zfs-keygen'", remote[2])
            self.assertLess(remote[2].index('assert '), remote[2].index('os.execv('))
            os.write(kwargs['stdout'].fileno(), CANARY)
            if mode == 'timeout':
                raise subprocess.TimeoutExpired('PUBLIC_SSH_FIXTURE', 60)
            return types.SimpleNamespace(returncode=7 if mode == 'failure' else 0)

        argv = [str(SOURCE), '--ssh-config', str(self.config), '--source', alias,
                '--destination', str(self.destination), '--expected-source-boot-id', boot]
        output = io.StringIO()
        with patch.object(sys, 'argv', argv), patch.object(m.os, 'geteuid', return_value=0), \
                patch.object(Path, 'read_text', public_guard_read), \
                patch.object(Path, 'lstat', fixture_root_owner), \
                patch.object(m.resource, 'setrlimit'), \
                patch.object(m.ctypes, 'CDLL', return_value=types.SimpleNamespace(prctl=lambda *a: 0)), \
                patch.object(m.subprocess, 'run', side_effect=fake_ssh), \
                contextlib.redirect_stdout(output):
            m.main()
        return output.getvalue()

    def test_fresh_strict_ssh_and_binary_direct_fd(self):
        output = self.run_receiver()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.destination.read_bytes() == CANARY)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        report = json.loads(output)
        self.assertEqual(report['native_exit_status'], 0)
        self.assertFalse(report['private_hash_or_length_reported'])
        self.assertNotIn('PUBLIC_TRANSPORT_FIXTURE', output)

    def test_nonzero_generation_quarantines_destination(self):
        with self.assertRaises(RuntimeError):
            self.run_receiver('failure')
        self.assertTrue(self.destination.exists())
        self.assertTrue(self.destination.read_bytes() == CANARY)

    def test_timeout_quarantines_destination(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_receiver('timeout')
        self.assertTrue(self.destination.exists())

    def test_existing_destination_refuses_second_private_call(self):
        self.destination.write_bytes(CANARY)
        with self.assertRaises(FileExistsError):
            self.run_receiver()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.destination.read_bytes() == CANARY)

    def test_invalid_boot_id_refused_before_transport(self):
        with self.assertRaises(RuntimeError):
            self.run_receiver(boot='PUBLIC_INVALID_BOOT_ID')
        self.assertEqual(self.calls, [])
        self.assertFalse(self.destination.exists())

    def test_shell_alias_refused_before_transport(self):
        with self.assertRaises(RuntimeError):
            self.run_receiver(alias='public-fixture;false')
        self.assertEqual(self.calls, [])

    def test_real_help_cli_without_private_operations(self):
        result = subprocess.run([sys.executable, str(SOURCE), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--expected-source-boot-id', result.stdout)
        self.assertNotIn('PUBLIC_TRANSPORT_FIXTURE', result.stdout)

    def test_real_ssh_G_ignores_adversarial_mux_defaults(self):
        unwanted_mux = self.root / 'PUBLIC_UNWANTED_MUX'
        self.config.write_text('Host public-fixture\n HostName public-fixture.invalid\n'
                               f' ControlMaster auto\n ControlPath {unwanted_mux}\n'
                               ' ControlPersist 600\n IdentitiesOnly no\n')
        argv = ['ssh', '-G', '-F', str(self.config)]
        for value in CONTROLS:
            argv += ['-o', value]
        argv += ['public-fixture']
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        effective = dict(line.split(' ', 1) for line in result.stdout.splitlines())
        self.assertEqual(effective['controlmaster'], 'false')
        self.assertEqual(effective.get('controlpath', 'none'), 'none')
        self.assertEqual(effective['controlpersist'], 'no')
        self.assertEqual(effective['identitiesonly'], 'yes')
        self.assertEqual(effective['stricthostkeychecking'], 'true')
        self.assertNotIn('PUBLIC_UNWANTED_MUX', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
