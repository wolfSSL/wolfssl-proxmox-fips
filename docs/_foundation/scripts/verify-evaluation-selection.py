#!/usr/bin/env python3
"""Verify ten selected evaluation DEBs against an authenticated SHA256SUMS."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def manifest_rows(text):
    rows = {}
    for line in text.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64}) [ *]([A-Za-z0-9_+:.~/\-]+)', line)
        require(match is not None, 'malformed checksum row')
        digest, name = match.groups()
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and str(relative) == name
                and not any(part in ('.', '..') for part in relative.parts),
                'checksum path is not a canonical relative path')
        require(name not in rows, 'duplicate checksum path')
        rows[name] = digest
    require(bool(rows), 'empty checksum manifest')
    return rows


def public_file(root, name):
    path = root / name
    require(path.resolve(strict=True) == path and path.is_relative_to(root),
            'bundle member is a symlink or escapes the bundle')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == root.stat().st_uid and not info.st_mode & 0o022,
            'bundle member is not protected regular data')
    return path


def verify(root, selected, expected_checksums_sha256):
    require(re.fullmatch(r'[0-9a-f]{64}', expected_checksums_sha256) is not None,
            'expected checksum-manifest digest must be a SHA256 value')
    require(root.is_absolute() and root.resolve(strict=True) == root and root.is_dir(),
            'bundle must be a canonical absolute directory')
    require(not root.stat().st_mode & 0o022, 'bundle must not be group/world writable')
    manifest = public_file(root, 'SHA256SUMS')
    require(manifest.stat().st_size <= 1048576, 'oversized checksum manifest')
    manifest_bytes = manifest.read_bytes()
    require(hashlib.sha256(manifest_bytes).hexdigest() == expected_checksums_sha256,
            'checksum manifest differs from the independently supplied digest')
    rows = manifest_rows(manifest_bytes.decode('ascii'))
    # Validate every listed path before the guide invokes native sha256sum on it.
    for name in rows:
        public_file(root, name)
    require(len(selected) == 10 and len(set(selected)) == 10,
            'select exactly ten distinct package paths')
    names = []
    for path in selected:
        require(path.is_absolute() and path.parent == root
                and re.fullmatch(r'[A-Za-z0-9_+:.~\-]+\.deb', path.name) is not None,
                'selected package must be a direct DEB member of the bundle')
        names.append(path.name)
    require(set(names) == {path.name for path in root.glob('*.deb')},
            'bundle DEB inventory differs from the selected ten packages')
    require(all(name in rows for name in names),
            'selected package is absent from the authenticated checksum manifest')
    for name in names:
        path = public_file(root, name)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_uid == root.stat().st_uid and not info.st_mode & 0o022,
                    'selected package changed protection')
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == rows[name],
                    'selected package checksum differs')
    return dict(status='TEN_SELECTED_DEBS_AUTHENTICATED_CHECKSUMS_MATCH',
                package_count=10, unique_manifest_rows=len(rows),
                extra_public_rows=len(rows) - 10, read_only=True,
                expected_checksum_manifest_digest_verified=True,
                authenticity='Expected digest must come from authenticated vendor delivery')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--checksums-sha256', required=True,
                        help='Expected public digest supplied through authenticated vendor delivery')
    parser.add_argument('packages', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.packages, args.checksums_sha256), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, UnicodeError) as error:
        print('evaluation selection refused: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
