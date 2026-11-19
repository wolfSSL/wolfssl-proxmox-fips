# wolfSSL FIPS for Proxmox VE — Installation

Install the wolfSSL kernel, cryptographic libraries and Proxmox integration from the wolfSSL
APT repository. Packages supply the binaries, signatures and service configuration; no source
build or local patching is required.

**Note:** This APT deployment model is still being evaluated for production readiness, and issues
may remain. Send feedback and relevant diagnostic logs to `support@wolfssl.com`. Remove
credentials, private keys and other sensitive information before sharing.

Complete this guide on each node, then configure its storage with the ZFS, LUKS or Ceph guide.
For a cluster, read [Cluster planning](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md)
before installing the first node.

## 1. Requirements and repository choice

Start with a fresh Proxmox VE 9.2 installation on amd64, updated from the appropriate
[Proxmox repositories](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html#sysadmin_package_repositories).
The [Proxmox installation guide](https://pve.proxmox.com/pve-docs/chapter-pve-installation.html)
covers the base installation. Run commands as root on the node unless labeled otherwise.

Have console access throughout installation and the first reboot. Use separate data storage
for encrypted guest disks. An ext4/LVM root keeps storage-encryption recovery separate from
booting the host. Public FIPS Ready Ceph nodes can instead follow the
[unencrypted ZFS boot-mirror evaluation](#evaluate-a-zfs-boot-mirror-on-a-ceph-node) below.
Encrypted root installations are outside this guide's scope.

This edition covers fresh nodes and new clusters made entirely of wolfSSL-configured nodes.
Converting an existing production node or manual-build installation in place and mixing stock
and wolfSSL nodes in one cluster are unsupported. Configured nodes return to stock Proxmox by
reinstallation. Before guest, storage or cluster configuration, an unused standalone Foundation
node can use the limited [channel-change](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#5-change-a-ready-foundation-node-to-licensed-v5)
or [stock-return](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#6-return-an-unused-foundation-node-to-stock) procedure.
Guests can be imported onto a freshly prepared target using the migration guides.

Choose one repository for the installation:

| Channel | Repository URI | Purpose |
| --- | --- | --- |
| FIPS Ready | `https://packages.wolfssl.com/debian` | Evaluation and compatibility testing. This is not a FIPS-validated module. |
| Licensed FIPS v5 | `https://packages.wolfssl.com/fips-v5/debian` | Licensed wolfCrypt FIPS modules. Obtain access and the applicable module documentation from wolfSSL. |

Use the same channel on every cluster node. Install the licensed channel directly when it is
the intended deployment; do not configure both sources together. Confirm the module versions,
approved services and operating-environment requirements with wolfSSL against the applicable
Security Policy. Installing these packages does not validate the whole operating system or
Proxmox deployment.

If Foundation was completed with Ready but licensed v5 is required, follow
[Change a Ready Foundation node to licensed v5](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#5-change-a-ready-foundation-node-to-licensed-v5)
before continuing to storage or cluster configuration. This is a repository replacement and
full upgrade; uninstalling Ready first is unnecessary.

The integration configures web and API TLS, SSH, corosync, the Ceph daemons, QEMU, the Proxmox
Backup Server clients, the kernel random-number path and the selected storage to use wolfCrypt.
Other OpenSSL programs use OpenSSL's default provider, outside wolfCrypt, without error.
These include `pvedaemon` (login tickets), the SSH client used by migration and replication,
APT and administrator shells; `wolfssl-fips-pve-verify` lists those running. Guest operating
systems are unchanged. Virtual TPM cryptography remains outside the wolfCrypt validated
boundary.
Repository signature verification also has exceptions: licensed APT and `pveam` on both
channels use Sequoia (`sqv`), outside wolfCrypt.

### Evaluate a ZFS boot mirror on a Ceph node

This evaluation uses the public FIPS Ready channel on a fresh Proxmox VE 9.2 node. It does
not qualify the licensed channel, a Ready-to-v5 conversion or production support. The root
filesystem remains unencrypted; encrypted Ceph OSDs do not encrypt the root or monitor store.

Before starting, obtain from wolfSSL a FIPS Ready evaluation package set compatible with
the installed Proxmox release, including the corrected checker described in section 5 and
the required rebuilt Proxmox dependencies. If APT cannot resolve the package dependencies,
obtain a compatible wolfSSL package set before proceeding.

Select ZFS RAID1 on two boot disks in the Proxmox installer, with separate devices reserved
for Ceph OSDs. Use UEFI with the signed shim/GRUB Secure Boot path from section 4. Keep
console access available and both boot disks' EFI System Partitions synchronized using
[Proxmox boot management](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html#sysboot_proxmox_boot_tool).

Select the Ceph profile in section 5. The wolfSSL kernel supplies stock ZFS modules for the
boot pool. Do not install `wolfssl-fips-pve-zfs`, `wolfzfs-kmod` or
`wolfssl-fips-pve-apt-zfs` for this layout. Those packages select encrypted wolfZFS guest
storage. Ceph alone provides OSD encryption. To reproduce an install-media selection that
also includes the LUKS metapackage, use the Ceph-plus-LUKS row in section 5. The additional
metapackage does not encrypt the root or require creating standalone local volumes;
this retained-LUKS case requires the checker correction described there. Configure
standalone LUKS volumes only when separate local encrypted storage is needed.

An ISO builder should select the boot filesystem independently of the encrypted guest-storage
profile: allow installer ZFS RAID1 with the Ceph profile, while retaining the guard against
installing the conflicting Ceph and wolfZFS guest-storage metapackages together. The reference
installation uses official Proxmox media followed by APT installation; a custom ISO needs
its own installation and boot verification.

Keep guest disks and backups on the encrypted destinations selected by the Ceph and Backup
guides. Disable the installer's unencrypted `local-zfs` guest destination as described in
the Ceph guide. The limited stock-return helper does not cover a root mirror with managed
ESPs; return a configured node to stock by reinstallation.

## 2. Prepare SSH access

The packaged SSH configuration accepts ECDSA and RSA keys rather than Ed25519. Installing
OpenSSH restarts the service immediately, before the node reboot. Authorize a suitable key
for every administrative account and test it in a new connection first.

For example, on the administrator's workstation:

```sh
ssh-keygen -t ecdsa -b 384 -f ~/.ssh/id_pve_fips
ssh-copy-id -i ~/.ssh/id_pve_fips.pub root@<node>
ssh -o IdentitiesOnly=yes -o PreferredAuthentications=publickey -i ~/.ssh/id_pve_fips root@<node> true
```

Keep the console available until this login succeeds again after installation and reboot.

## 3. Add the repository and preferences

Download the archive-key package and extract only its public key. Do not install the downloaded
package or extract its filesystem onto the node:

```sh
curl -fsSLO https://packages.wolfssl.com/keyring/wolfssl-fips-eval-archive-keyring_latest_all.deb
dpkg-deb --fsys-tarfile wolfssl-fips-eval-archive-keyring_latest_all.deb \
  | tar -xOf - ./usr/share/keyrings/wolfssl-fips-eval-archive-keyring.gpg \
  > wolfssl-fips-eval-archive-keyring.gpg
gpg --show-keys --with-fingerprint ./wolfssl-fips-eval-archive-keyring.gpg
```

Stop if downloading, extraction or key inspection fails. Require exactly one primary key, with
fingerprint `FA9A C397 4B00 99D4 15C7 3673 19D1 F0DB 1F30 4C7B`; confirm it against the copy
supplied by your wolfSSL contact. Stop if it differs or additional primary keys are present.
Both channels use this archive key; it is separate from the kernel signing key in section 4.

After verifying the key, install only that public-key file:

```sh
install -m 0644 wolfssl-fips-eval-archive-keyring.gpg /usr/share/keyrings/wolfssl-fips-eval-archive-keyring.gpg
```

Create `/etc/apt/sources.list.d/wolfssl-fips-pve.sources`. The example selects FIPS Ready;
for the licensed channel, replace the URI with the licensed URI in section 1.

```sh
cat > /etc/apt/sources.list.d/wolfssl-fips-pve.sources <<'REPO'
Types: deb
URIs: https://packages.wolfssl.com/debian
Suites: pve9
Components: main
Signed-By: /usr/share/keyrings/wolfssl-fips-eval-archive-keyring.gpg FA9AC3974B0099D415C7367319D1F0DB1F304C7B
REPO
```

The fingerprint restricts this source to the verified signing key. If wolfSSL rotates the
primary key, confirm its replacement fingerprint before updating the key file and `Signed-By`.
APT verifies signed repository metadata before installing packages; do not bypass signature
errors or install the downloaded archive-key package as a workaround.

Nodes intended for Ceph also need `ceph-tentacle` on the `Components:` line:

```text
Components: main ceph-tentacle
```

Install the preference package in its own APT transaction:

```sh
apt update
apt install wolfssl-fips-pve-apt
apt update
```

**For encrypted ZFS guest storage**, use this install command instead of the single-package command above:

```sh
apt install wolfssl-fips-pve-apt wolfssl-fips-pve-apt-zfs
```

APT reads preferences when it starts. Complete this separate transaction before installing
other wolfSSL packages, and do not run a general upgrade between adding the source and
completing section 5. The preferences select compatible rebuilt packages, including packages
whose versions sort below their Debian or Proxmox counterparts.

## 4. Enroll the kernel signing key

```sh
mokutil --sb-state
```

With Secure Boot disabled, enrollment is unnecessary. With Secure Boot enabled, the node
must trust the wolfSSL kernel signing key before it can boot the packaged kernel. Secure
Boot must use the signed shim/GRUB path described in
[Proxmox boot management](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html#sysboot_secure_boot).

Download the certificate and check its SHA-256 digest:

```sh
curl -fsSLO https://packages.wolfssl.com/keys/proxmox-kernel/wolfssl-kernel-signing-proxmox-eval.der
sha256sum wolfssl-kernel-signing-proxmox-eval.der
```

Expected digest:

```text
4b9e012ec8f6b5ba9f611d9fb685e9e7a35e95eb182e6693bdae6a2570374d25
```

Stop on a mismatch. Enrolling this certificate trusts kernels signed by wolfSSL; confirm the
fingerprint against the copy supplied by your wolfSSL contact. The certificate's `eval` name
also applies to the licensed channel.

Check whether the key is already trusted:

```sh
mokutil --test-key wolfssl-kernel-signing-proxmox-eval.der
```

Read its output. `is already enrolled` or `is already in db` means no enrollment is needed.
`is already in the enrollment request` means confirmation is still pending. Do not infer
trust from the command's exit status.

If the key is not trusted or queued, queue it now, choosing a one-time enrollment password:

```sh
mokutil --timeout -1
mokutil --import wolfssl-kernel-signing-proxmox-eval.der
```

Do not reboot yet. Confirm the request at the console during section 6. Enrollment is needed
once per node, not for each kernel update. TPM-sealed boot secrets and third-party DKMS
modules need additional planning; consult the
[kernel signing-key guide](https://packages.wolfssl.com/HOWTO-proxmox-kernel-key.md#before-you-start)
before proceeding if either is used.

## 5. Install the packages

Install the metapackage matching the storage selected for this node. Each storage
metapackage brings in the shared kernel, provider, service configuration and wrappers.

| Storage profile | Install command |
| --- | --- |
| Encrypted ZFS guest storage | `apt install --allow-downgrades wolfssl-fips-pve-zfs` |
| LUKS data volumes | `apt install --allow-downgrades wolfssl-fips-pve-luks` |
| Ceph Tentacle | `apt install --allow-downgrades wolfssl-fips-pve-ceph` |
| Ceph plus LUKS metapackage | `apt install --allow-downgrades wolfssl-fips-pve-ceph wolfssl-fips-pve-luks` |
| No encrypted data storage yet | `apt install --allow-downgrades proxmox-kernel-wolfcrng wolfssl-fips-pve-config wolfssl-fips-pve-wrappers` |

For Ceph, first complete the stock Tentacle package preparation in the
[Ceph guide](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md). Do not install the Ceph and
wolfZFS metapackages together. The wolfZFS metapackage and the additional ZFS pin are for
encrypted ZFS guest storage. They are not required merely because ZFS packages are present.
For encrypted ZFS guest storage, confirm both pin packages were installed in section 3.

For the Ready ZFS boot-mirror evaluation, use the Ceph row, or the Ceph-plus-LUKS row to
reproduce media that includes both metapackages. Use only the general preference package;
the LUKS metapackage does not select wolfZFS. Configure separate local LUKS volumes with
the LUKS guide only if they are needed. If none are configured, use a
`wolfssl-fips-pve-config` release whose checker distinguishes Ceph-managed OSDs from
standalone local volumes. Check 23 must report
that standalone checks are skipped because none are configured; check 24 must still verify
the encrypted Ceph OSDs after cluster creation. If the installed checker instead demands
local crypttab volumes, unlock units, mounts or backup staging, obtain the corrected package
from wolfSSL before using the retained-LUKS case. Do not ignore these failures or create
an unrelated local volume to satisfy them.

On every node, also install the compatible backup clients and SSH packages:

```sh
apt install --allow-downgrades proxmox-backup-client proxmox-backup-file-restore libproxmox-backup-qemu0
apt install --allow-downgrades openssh-server openssh-client openssh-sftp-server
```

`DOWNGRADING:` messages are expected for compatible rebuilds selected by the preferences.
Review the transaction; do not accept removal of the integration or the intended storage
packages. Keep the local `sshd_config` if prompted.

The packages configure the provider and service settings automatically. They also install
compatible Proxmox authentication and supporting library packages. Do not add the old
manual-build wrappers or provider configuration on top of them.

Open a fresh SSH connection with the key from section 2. If the node previously advertised
an Ed25519 host key, the workstation's SSH client refuses the connection because the host key
changed. Verify the node's ECDSA or RSA host-key fingerprint at the console. For the ECDSA
fingerprint:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub
```

On the workstation, remove the node's old entry, replacing `<node>` with the host name or
address in the SSH client's warning, then reconnect. Accept the offered key only if its
fingerprint matches:

```sh
ssh-keygen -R <node>
```

## 6. Reboot and verify

```sh
reboot
```

If a signing-key request is queued, attend the console. In **Perform MOK management**, select
**Enroll MOK**, view the key and confirm its subject is
`wolfSSL Proxmox VE Kernel Signing (eval)`. The displayed SHA-1 fingerprint is:

```text
28 78 AC E2 7B DD 8D 2D 12 A9 10 90 21 F1 BE 90 3F 04 39 34
```

Select **Continue**, **Yes**, enter the one-time password, and select **Reboot**. Complete
confirmation promptly; firmware may restart the node while the menu waits. If enrollment is
missed and the kernel fails with `bad shim signature`, boot a stock Proxmox kernel from the
advanced boot menu, queue the key again, and retry at the console.

After boot, reconnect and check:

```sh
uname -r
cat /proc/sys/crypto/fips_enabled
systemctl is-active pve-guests
wolfssl-fips-pve-verify --login
```

The running kernel must contain `+wolfcrng`, and `fips_enabled` must be `1`.
Wait for `pve-guests` to become `active` before running the verifier. With `--login`, the
verifier prompts for the root password and checks management login as well as configuration.
Require the kernel, wolfCrypt/provider, SSH and management-login checks to pass. Before
encrypted storage is created, expect failures for missing storage or unlock configuration and
for guest or backup content enabled on installer-created storage; Ceph-prepared nodes report
a warning for that content. LUKS nodes also report unset encrypted container-backup staging.
Resolve all other `FAIL` results before continuing. Complete the selected storage guide, then
repeat verification and resolve those deferred failures.
Cluster checks follow cluster configuration.
On Ceph-prepared nodes, the no-running-Ceph-daemon check is expected until the new Ceph cluster
is initialized; repeat verification afterward.

For the Ready ZFS boot-mirror evaluation, also check the installer-created root pool and
boot configuration after this reboot:

```sh
findmnt --mountpoint / -o SOURCE,FSTYPE
zpool status -P rpool
zfs get -H -o name,property,value encryption rpool/ROOT/pve-1
zfs version
modinfo -F filename zfs
modinfo -F depends zfs
proxmox-boot-tool status
```

Require an unencrypted ZFS root, an ONLINE mirror with both intended leaves available and
no known data errors. Record the device READ/WRITE/CKSUM counters and any pool warning;
use the [mirror-recovery checks](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#44-recover-a-zfs-boot-mirror)
to assess a returning member. Require matching OpenZFS userspace and kernel releases, and
a stock module in the running kernel's `zfs/` directory with dependency `spl`. Both ESPs
must list GRUB and the intended `+wolfcrng` kernel. An ONLINE mirror alone does not establish
that both disks are bootable; complete the site's boot-disk failure test before relying
on redundancy. Use the
[Proxmox bootable-disk replacement procedure](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html#sysadmin_zfs_change_failed_dev)
for pool replacement and ESP recovery.

## 7. Configure storage and operations

The first reboot activates the selected profile. Continue with its guide to create encrypted
storage and configure unlock or cluster services:

- [ZFS native encryption](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-ZFS.md).
- [LUKS/dm-crypt](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-LUKS.md).
- [Ceph](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md), after forming the new cluster.

The installer-created `local`, `local-lvm` or `local-zfs` storage is not made encrypted by
installing the common packages. Keep guest data and backups on the encrypted destinations
configured in the selected guide.

When creating or importing VMs, use standard VGA/noVNC or another supported display.
SPICE/QXL display, SPICE audio and SPICE USB redirection are unsupported; the verifier
identifies affected VM settings and supplies their removal commands.

Complete [Backup](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md) before relying on the deployment.
Use [Node maintenance and recovery](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md)
for subsequent updates and access recovery. Before configuring storage or a cluster, use its
[licensed-v5 channel change](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#5-change-a-ready-foundation-node-to-licensed-v5)
if the repository choice needs correcting. Its
[stock-return procedure](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#6-return-an-unused-foundation-node-to-stock)
is limited to unused standalone Foundation nodes with a supported GRUB boot configuration.
