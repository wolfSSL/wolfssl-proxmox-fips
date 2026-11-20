# Encrypted-root documentation tests

Run these offline checks from the repository root in a Linux environment with
Python 3.11 or newer, Bash, GNU coreutils and an OpenSSH client:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/encrypted-zfs-root -p 'test_*.py' -v
```

The 50 tests exercise the public package-selection and key-receiver helpers and
the guide's actual evaluation-preference and provisioning-guard commands. They
use synthetic public package bytes and metadata, temporary directories, mocked generation/transport
and retained public checksum-manifest text. No operational key is generated or
read, no package is built or installed, and no network connection is made.
The transport test's real `ssh -G` invocation only expands a temporary local
configuration; it does not contact its fixture hostname. No root privileges or
Proxmox installation are required.

These fixtures check documented failure handling, exact package selection,
protected output, independent checksum expectations and atomic preference-file
replacement. The provisioning fixtures require active transactions, unexpected
mounts or existing target pools/snapshots to stop before the next protected
action. They distinguish a confirmed absence from inspection errors, including
failed inventories with empty or partial output. They do not establish that a
current evaluation bundle exists or replace the native fresh-install, boot,
APT-policy and mirror-recovery walkthrough.
