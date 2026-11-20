#!/usr/bin/env python3
"""Receive a fresh ZFS root wrapping key into protected external custody."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import resource
import re
import shlex
import stat
import subprocess
import sys
import uuid


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-config', required=True, type=Path)
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--expected-source-boot-id',
                        help='Require this canonical boot UUID in the actual keygen session')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'run the custody receiver as root')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.source) is not None,
            'source must be an explicit SSH-config alias')
    if args.expected_source_boot_id is not None:
        try:
            parsed_boot_id = uuid.UUID(args.expected_source_boot_id)
        except (ValueError, AttributeError):
            raise RuntimeError('expected source boot ID must be a canonical UUID')
        require(str(parsed_boot_id) == args.expected_source_boot_id
                and parsed_boot_id.version == 4 and parsed_boot_id.variant == uuid.RFC_4122,
                'expected source boot ID must be a canonical version4 UUID')
    require(args.ssh_config.is_absolute() and args.ssh_config.resolve() == args.ssh_config,
            'SSH config must be a canonical absolute file')
    config_metadata = args.ssh_config.lstat()
    require(stat.S_ISREG(config_metadata.st_mode) and config_metadata.st_uid == 0
            and not config_metadata.st_mode & 0o022,
            'SSH config must be a protected root-owned regular file')
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    require(ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) == 0,
            'cannot disable process dumps')
    require(not Path('/proc/sys/kernel/core_pattern').read_text().strip().startswith('|')
            and Path('/proc/sys/fs/suid_dumpable').read_text().strip() == '0',
            'establish the management-host non-piped core custody guard first')
    require(not Path('/proc/swaps').read_text().splitlines()[1:],
            'this encrypted-root profile requires no active management-host swap')
    destination = args.destination
    require(destination.is_absolute() and destination.parent.resolve() == destination.parent,
            'destination parent must be an existing canonical external-custody directory')
    metadata = destination.parent.lstat()
    require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o077,
            'external-custody directory must be root-owned mode0700')
    ssh = ['ssh', '-T', '-F', str(args.ssh_config), '-o', 'BatchMode=yes',
           '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ControlPersist=no',
           '-o', 'IdentitiesOnly=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlgorithms=ecdsa-sha2-nistp256',
           '-o', 'PubkeyAcceptedAlgorithms=ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521',
           args.source]
    # No private input exists yet. Refuse missing/mismatched host trust through SSH.
    ready = subprocess.run([*ssh, 'wolfssl-fips-zfs-root-prepare --check'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=60)
    require(ready.returncode == 0, 'source public readiness refused')
    remote_code = 'import os\nfrom pathlib import Path\n'
    if args.expected_source_boot_id is not None:
        remote_code += ("assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == "
                        + repr(args.expected_source_boot_id) + '\n')
    remote_code += ("os.environ['OPENSSL_CONF'] = '/etc/wolfprovider/openssl.cnf'\n"
                    "os.execv('/usr/sbin/wolfssl-fips-zfs-keygen', "
                    "['/usr/sbin/wolfssl-fips-zfs-keygen', '--stdout'])\n")
    remote_keygen = shlex.join(['/usr/bin/python3', '-c', remote_code])
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    # The receiver does not read, format, hash or measure private output. SSH writes
    # directly to the exclusive external destination, never to a terminal or log.
    with os.fdopen(descriptor, 'wb') as stream:
        result = subprocess.run([*ssh, remote_keygen], stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.DEVNULL, timeout=60)
        native_status = result.returncode
        require(native_status == 0,
                'private generation/transfer failed; quarantine this destination and do not reuse it')
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    print(json.dumps(dict(status='EXTERNAL_KEY_RECEIVED_DURABLY', native_exit_status=native_status,
        private_content_read_by_receiver=False, private_output_logged=False,
        private_hash_or_length_reported=False, separate_recovery_copy_verified=False)))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        # The errors contain only public command/path/status information.
        print('external key custody refused: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
