"""Run the guide's actual absence guards on public command-result fixtures."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / 'docs/_foundation/WOLFSSL-PROXMOX-FIPS-ENCRYPTED-ZFS-ROOT.md'
TEXT = GUIDE.read_text()
SHELL_BLOCKS = re.findall(r'```sh\n(.*?)\n```', TEXT, re.S)


def section(number):
    return TEXT.split(f'## {number}. ', 1)[1].split('\n## ', 1)[0]


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


QUIESCE = section(6)
PACKAGE_GUARD = between(QUIESCE, 'systemctl stop apt-daily.timer apt-daily-upgrade.timer\n',
                        'for unit in pveproxy ')
MOUNT_GUARD = between(QUIESCE, 'test "$(systemctl is-active pve-cluster || true)" = inactive\n',
                      '\n```')
SNAPSHOT_GUARD = between(QUIESCE, 'snapshot=root-provision-copy\n',
                         'sync\nzfs snapshot ')
POOL_GUARDS = [
    between(section(2), 'test ! -e "$target_root"\n',
            'install -d -m 0700 /root/root-provisioning'),
    between(section(5), 'test ! -e "$target_root"\n',
            'install -d -m 0700 "$target_root"'),
    between(section(9), 'zpool export "$target_pool"\n', 'poweroff\n'),
]
PROTECTED = 'PUBLIC_PROTECTED_ACTION_REACHED'
SWAP_GUARDS = re.findall(r'^swap_devices=\$\(swapon --noheadings --show=NAME\)\n'
                         r'test -z "\$swap_devices"$', TEXT, re.M)
AUDIT_GUARDS = re.findall(r'^package_audit=\$\(dpkg --audit\)\n'
                          r'test -z "\$package_audit"$', TEXT, re.M)
GUEST_GUARD = between(QUIESCE, 'test ! -e /etc/pve/corosync.conf\n',
                      'pvesm set local-zfs --disable 1\n')


class ProvisioningGuardsTests(unittest.TestCase):
    def guide_strict_setup(self, guard):
        contexts = [block for block in SHELL_BLOCKS if guard.strip() in block]
        self.assertTrue(contexts, 'Guard was not extracted from a guide shell fence')
        for block in contexts:
            self.assertTrue(block.startswith('set -euo pipefail\n'),
                            'Every affected guide fence must set its own strict options')
            if "cat > /root/root-provisioning/create-target-pool.sh <<'SH'" in block:
                embedded = between(block, "<<'SH'\n", '\nSH\n')
                self.assertTrue(embedded.startswith('#!/bin/bash\nset -euo pipefail\n'),
                                'The standalone pool creator must set its own strict options')
        # The fresh test shell receives only a header present in the actual guide.
        return contexts[0].splitlines(keepends=True)[0]

    def run_guard(self, guard, command, arguments, status, output=''):
        # Each command is replaced by a shell function with independently supplied
        # output/status. Nothing touches services, pools, mounts or package state.
        stub = f'''{command}() {{
  test "$*" = {shlex.quote(arguments)} || exit 97
  printf '%s' {shlex.quote(output)}
  return {status}
}}
'''
        script = (self.guide_strict_setup(guard) + stub +
                  'target_pool=rootpool\nsnapshot=root-provision-copy\n' + guard + '\n' +
                  f"printf '%s\\n' {shlex.quote(PROTECTED)}\n")
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('SHELLOPTS', 'BASHOPTS', 'BASH_ENV')}
        syntax = subprocess.run(['bash', '-n'], input=script, capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        return subprocess.run(['bash', '--noprofile', '--norc'], input=script,
                              capture_output=True, text=True, env=environment)

    def expect_stopped(self, result, status=None):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotEqual(result.returncode, 97, 'Fixture command arguments did not match')
        self.assertNotIn(PROTECTED, result.stdout)
        if status is not None:
            self.assertEqual(result.returncode, status, result.stderr)

    def expect_continued(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(PROTECTED, result.stdout)

    def test_active_package_transaction_stops_before_service_shutdown(self):
        self.expect_stopped(self.run_guard(PACKAGE_GUARD, 'pgrep',
                            '-x apt|apt-get|dpkg|unattended-upgr', 0, '12345\n'), 1)

    def test_no_package_transaction_continues(self):
        self.expect_continued(self.run_guard(PACKAGE_GUARD, 'pgrep',
                              '-x apt|apt-get|dpkg|unattended-upgr', 1))

    def test_package_query_syntax_error_stops(self):
        self.expect_stopped(self.run_guard(PACKAGE_GUARD, 'pgrep',
                            '-x apt|apt-get|dpkg|unattended-upgr', 2), 2)

    def test_package_query_fatal_error_stops(self):
        for status in (3, 7):
            with self.subTest(status=status):
                self.expect_stopped(self.run_guard(PACKAGE_GUARD, 'pgrep',
                                    '-x apt|apt-get|dpkg|unattended-upgr', status), status)

    def test_pmxcfs_still_mounted_stops(self):
        self.expect_stopped(self.run_guard(MOUNT_GUARD, 'mountpoint', '-q /etc/pve', 0))

    def test_pmxcfs_confirmed_unmounted_continues(self):
        self.expect_continued(self.run_guard(MOUNT_GUARD, 'mountpoint', '-q /etc/pve', 32))

    def test_pmxcfs_inspection_error_stops(self):
        for status in (1, 2, 7):
            with self.subTest(status=status):
                self.expect_stopped(self.run_guard(MOUNT_GUARD, 'mountpoint', '-q /etc/pve', status))

    def test_target_pool_absent_from_successful_inventory_continues(self):
        for number, guard in enumerate(POOL_GUARDS):
            for output in ('', 'sourcepool\nrootpool2\nbackup-rootpool\n'):
                with self.subTest(guard=number, output=output):
                    self.expect_continued(self.run_guard(guard, 'zpool', 'list -H -o name', 0, output))

    def test_target_pool_in_successful_inventory_stops(self):
        for number, guard in enumerate(POOL_GUARDS):
            with self.subTest(guard=number):
                self.expect_stopped(self.run_guard(guard, 'zpool', 'list -H -o name', 0,
                                    'sourcepool\nrootpool\nrootpool2\n'))

    def test_failed_pool_inventory_stops_even_with_empty_or_partial_output(self):
        for number, guard in enumerate(POOL_GUARDS):
            for status in (1, 2, 7):
                for output in ('', 'sourcepool\n'):
                    with self.subTest(guard=number, status=status, output=output):
                        self.expect_stopped(self.run_guard(guard, 'zpool',
                                            'list -H -o name', status, output), status)

    def test_snapshot_absent_from_successful_inventory_continues(self):
        for output in ('', 'sourcepool/ROOT/pve-1@old\n'):
            with self.subTest(output=output):
                self.expect_continued(self.run_guard(SNAPSHOT_GUARD, 'zfs',
                                      'list -H -t snapshot -o name', 0, output))

    def test_snapshot_present_in_successful_inventory_stops(self):
        self.expect_stopped(self.run_guard(SNAPSHOT_GUARD, 'zfs',
                            'list -H -t snapshot -o name', 0,
                            'sourcepool/ROOT/pve-1@old\nsourcepool/var-lib-vz@root-provision-copy\n'))

    def test_different_snapshot_name_does_not_false_match(self):
        self.expect_continued(self.run_guard(SNAPSHOT_GUARD, 'zfs',
                              'list -H -t snapshot -o name', 0,
                              'sourcepool/ROOT/pve-1@root-provision-copy-old\n'))

    def test_failed_snapshot_inventory_stops_even_with_empty_or_partial_output(self):
        for status in (1, 2, 7):
            for output in ('', 'sourcepool/ROOT/pve-1@old\n'):
                with self.subTest(status=status, output=output):
                    self.expect_stopped(self.run_guard(SNAPSHOT_GUARD, 'zfs',
                                        'list -H -t snapshot -o name', status, output), status)

    def test_successful_empty_swap_inventory_continues_at_all_three_sites(self):
        self.assertEqual(len(SWAP_GUARDS), 3)
        for number, guard in enumerate(SWAP_GUARDS):
            with self.subTest(guard=number):
                self.expect_continued(self.run_guard(guard, 'swapon', '--noheadings --show=NAME', 0))

    def test_active_swap_stops_before_private_or_pool_operations(self):
        self.assertEqual(len(SWAP_GUARDS), 3)
        for number, guard in enumerate(SWAP_GUARDS):
            with self.subTest(guard=number):
                self.expect_stopped(self.run_guard(guard, 'swapon', '--noheadings --show=NAME',
                                    0, '/dev/PUBLIC-swap-fixture\n'))

    def test_failed_swap_inspection_stops_with_original_status(self):
        self.assertEqual(len(SWAP_GUARDS), 3)
        for number, guard in enumerate(SWAP_GUARDS):
            for status in (1, 2, 7):
                for output in ('', '/dev/PUBLIC-swap-fixture\n'):
                    with self.subTest(guard=number, status=status, output=output):
                        self.expect_stopped(self.run_guard(guard, 'swapon',
                                            '--noheadings --show=NAME', status, output), status)

    def test_successful_empty_package_audit_continues_at_all_three_sites(self):
        self.assertEqual(len(AUDIT_GUARDS), 3)
        for number, guard in enumerate(AUDIT_GUARDS):
            with self.subTest(guard=number):
                self.expect_continued(self.run_guard(guard, 'dpkg', '--audit', 0))

    def test_dirty_package_state_stops_before_protected_operations(self):
        self.assertEqual(len(AUDIT_GUARDS), 3)
        for number, guard in enumerate(AUDIT_GUARDS):
            with self.subTest(guard=number):
                self.expect_stopped(self.run_guard(guard, 'dpkg', '--audit',
                                    0, 'PUBLIC-half-configured-package\n'))

    def test_failed_package_audit_stops_with_original_status(self):
        self.assertEqual(len(AUDIT_GUARDS), 3)
        for number, guard in enumerate(AUDIT_GUARDS):
            for status in (1, 2, 7):
                for output in ('', 'PUBLIC-half-configured-package\n'):
                    with self.subTest(guard=number, status=status, output=output):
                        self.expect_stopped(self.run_guard(guard, 'dpkg', '--audit',
                                            status, output), status)

    def test_successful_empty_guest_inventory_continues(self):
        self.expect_continued(self.run_guard(GUEST_GUARD, 'find',
                              '-L /etc/pve/qemu-server /etc/pve/lxc -maxdepth 1 -name *.conf -print', 0))

    def test_configured_guest_stops_before_source_shutdown(self):
        self.expect_stopped(self.run_guard(GUEST_GUARD, 'find',
                            '-L /etc/pve/qemu-server /etc/pve/lxc -maxdepth 1 -name *.conf -print',
                            0, '/etc/pve/qemu-server/PUBLIC-guest.conf\n'))

    def test_failed_guest_inventory_stops_with_original_status(self):
        for status in (1, 2, 7):
            for output in ('', '/etc/pve/qemu-server/PUBLIC-guest.conf\n'):
                with self.subTest(status=status, output=output):
                    self.expect_stopped(self.run_guard(GUEST_GUARD, 'find',
                                        '-L /etc/pve/qemu-server /etc/pve/lxc -maxdepth 1 -name *.conf -print',
                                        status, output), status)


if __name__ == '__main__':
    unittest.main(verbosity=2)
