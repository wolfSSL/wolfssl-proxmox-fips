# Experimental encrypted ZFS root for a Ceph node

Provision a new encrypted ZFS host-root mirror with the separate Ceph guest-storage
profile. This experimental FIPS Ready procedure uses UEFI GRUB through signed
shim/MOK, two Proxmox-managed VFAT ESPs, native AES-256-GCM and an external random
hex wrapping key entered at the attended boot prompt. The guest-ZFS/Ceph
metapackage conflict remains unchanged; do not install `wolfssl-fips-pve-zfs`.

This is a separate evaluation path, outside the qualified root layouts in
[Foundation](WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#1-requirements-and-repository-choice).
It does not add production support, installer-ISO encryption, a FIPS certificate,
an approved operating environment or whole-system approval. BIOS, systemd-boot,
existing production-root conversion, passphrase-derived root keys and active-root
package removal or stock conversion are outside this profile.

Run commands as root on the Proxmox node unless labeled **management host**.
Replace angle-bracket placeholders before running a block. Stop at a failed
command and inspect the state; disk creation and copy phases are not replayable.

## Prerequisites

- A fresh Proxmox VE 9.2 amd64 installation on temporary source disks, using the
  installer's unencrypted ZFS mirror. Require a separate ZFS dataset mounted at
  `/var/lib/vz`, no guests, no cluster membership and no initialized Ceph data.
- Two additional empty final boot disks with enough mirror capacity for the fresh
  OS, host data and free-space reserve. Keep dedicated NVMe OSD disks separate.
  This procedure never partitions those OSD disks.
- Secure Boot through the signed shim/GRUB path, console access for MOK enrollment
  and attended unlock, and independently verified ECDSA administrative SSH access.
- A Linux management host with Python 3, OpenSSH and separately mounted encrypted
  primary and recovery custody volumes. Verify their encryption, separate backing
  devices and recovery access using the site's storage controls before proceeding.
  They must remain outside both Proxmox filesystems and their boot disks.
- No active swap on the source or management host during private operations.
  Disable piped core collectors and process dumps as specified below. Do not use
  terminal/session recording for private input.
- A coherent experimental package bundle and checksum manifest obtained through
  wolfSSL's authenticated evaluation delivery. Authenticate the manifest using
  the delivery instructions before checking its hashes. The experimental packages
  are not assumed to be available from public APT. Do not use unsigned APT sources
  or substitute an unverified download.

The temporary source is plaintext provisioning media. Encryption of the final
root does not erase it. Retain it offline until acceptance, then retire it under
the site's media and credential policy. The final ESPs are also unencrypted;
neither they nor an initramfs may contain the operational wrapping key.

Use the example final pool name `rootpool`, distinct from the source pool. Change
it consistently if required. The root dataset is `rootpool/ROOT/pve-1`; the sibling
`rootpool/var-lib-vz` inherits the same encryption root.

## 1. Prepare and boot the provisioning source

Complete [Ceph package preparation](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md#1-prepare-the-ceph-packages)
before adding the wolfSSL source. Do not initialize Ceph yet. Follow Foundation
[SSH preparation](WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#2-prepare-ssh-access),
[repository authentication](WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#3-add-the-repository-and-preferences)
and [MOK enrollment preparation](WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#4-enroll-the-kernel-signing-key),
selecting only FIPS Ready and `Components: main ceph-tentacle`.

For this root profile, install both preference packages in their own transaction,
before any ZFS replacement. Use the experimental selection below in place of
Foundation's ordinary section-5 storage-profile selection.

```sh
set -euo pipefail
apt update
apt install wolfssl-fips-pve-apt wolfssl-fips-pve-apt-zfs
apt update
```

Stage the authenticated evaluation bundle at `/root/<evaluation-bundle>`. It must
be root-owned, with no group/world-writable members or symlinked paths, and
contain exactly one selected `.deb` for each of the ten package patterns below,
plus `SHA256SUMS`. Obtain the expected digest of that manifest through the
authenticated vendor delivery and replace the placeholder below. Debug packages
and other revisions belong outside this directory. The versioned signed kernel
payload is unchanged; it is included to bind the complete selected cohort.
Save the accompanying public [selection verifier](scripts/verify-evaluation-selection.py)
from the authenticated guide bundle at `/root/<guide-assets>/`. The checksum
manifest may also cover public provenance/authentication files; keep those files
and their relative paths intact.

The delivery must include compatible rebuilt `libpve-common-perl`,
`libpve-access-control` and `libpve-rs-perl` packages for the current Proxmox
baseline. These three packages are part of the authenticated selection below;
do not assume that the public repository already provides the revisions required
by this evaluation. Obtain one complete delivery and its independently supplied
manifest digest. Do not add packages to an older bundle or reuse its manifest
digest for a changed selection.

```sh
bundle=/root/<evaluation-bundle>
cd "$bundle"
checksums_sha256='<authenticated-sha256sums-sha256>'
evaluation_packages=(
  "$bundle"/proxmox-kernel-[0-9]*+wolfcrng*-pve_*_amd64.deb
  "$bundle"/wolfssl-fips-kmod-*_amd64.deb
  "$bundle"/wolfzfs-kmod-*_amd64.deb
  "$bundle"/proxmox-kernel-wolfcrng_*_amd64.deb
  "$bundle"/wolfzfs-kmod_*_amd64.deb
  "$bundle"/wolfssl-fips-pve-config_*_all.deb
  "$bundle"/wolfssl-fips-pve-zfs-root_*_amd64.deb
  "$bundle"/libpve-common-perl_*_all.deb
  "$bundle"/libpve-access-control_*_all.deb
  "$bundle"/libpve-rs-perl_*_amd64.deb
)
test "${#evaluation_packages[@]}" -eq 10
for package in "${evaluation_packages[@]}"; do test -f "$package"; done
python3 /root/<guide-assets>/verify-evaluation-selection.py \
  --bundle "$bundle" --checksums-sha256 "$checksums_sha256" \
  "${evaluation_packages[@]}"
sha256sum --strict --check SHA256SUMS
# Keep these authenticated revisions ahead of the public repository preferences.
evaluation_preferences=/etc/apt/preferences.d/99-wolfssl-encrypted-root-evaluation
if [ -e "$evaluation_preferences" ] || [ -L "$evaluation_preferences" ]; then
  test ! -L "$evaluation_preferences"
  test -f "$evaluation_preferences"
fi
evaluation_preferences_new=$(mktemp /etc/apt/preferences.d/.wolfssl-encrypted-root-evaluation.XXXXXX)
(
  set -euo pipefail
  declare -A evaluation_seen=()
  for package in "${evaluation_packages[@]}"; do
    package_name=$(/usr/bin/dpkg-deb --field "$package" Package)
    package_version=$(/usr/bin/dpkg-deb --field "$package" Version)
    [[ "$package_name" =~ ^[a-z0-9][a-z0-9+.-]+$ ]]
    [[ "$package_version" =~ ^[0-9][A-Za-z0-9.+:~-]*$ ]]
    test -z "${evaluation_seen[$package_name]+present}"
    evaluation_seen["$package_name"]=1
    printf 'Package: %s\nPin: version %s\nPin-Priority: 1002\n\n' \
      "$package_name" "$package_version"
  done
  test "${#evaluation_seen[@]}" -eq 10
) > "$evaluation_preferences_new"
chmod 0644 "$evaluation_preferences_new"
mv -T -- "$evaluation_preferences_new" "$evaluation_preferences"
selected=("${evaluation_packages[@]}"
  wolfssl-fips-pve-ceph wolfssl-fips-pve-luks
  proxmox-backup-client proxmox-backup-file-restore libproxmox-backup-qemu0
  openssh-server openssh-client openssh-sftp-server
)
apt-get -s install --allow-downgrades "${selected[@]}"
apt-get install --allow-downgrades "${selected[@]}"
test -z "$(dpkg --audit)"
grep -Fx ENABLED=no /etc/wolfssl-fips/zfs-root.conf
if /usr/lib/wolfssl-fips-zfs-root/update-cohort --probe; then
  probe_status=0
else
  probe_status=$?
fi
test "$probe_status" -eq 3
cat /etc/kernel/proxmox-boot-pin
dpkg-query -W -f='${Package}\t${Version}\n' \
  proxmox-kernel-wolfcrng wolfzfs-kmod wolfssl-fips-pve-config \
  wolfssl-fips-pve-zfs-root wolfssl-fips-pve-ceph wolfssl-fips-pve-luks
reboot
```

Review the simulated and actual transactions; do not accept removal of the
integration or intended storage packages. The LUKS metapackage is for separate
local volumes and may be omitted if none are required; Ceph OSD encryption already
includes its cryptsetup integration. Do not create or enable source root policy.

The separate evaluation preference file pins only the ten authenticated package
versions, leaving the published general preferences in place. It prevents a later
upgrade from selecting older published builds in place of this evaluation cohort.
Replace these exact pins only as part of a new approved, authenticated complete
delivery. A metadata-query failure leaves the existing preference file untouched;
inspect the retained hidden temporary file rather than installing its partial contents.

Attend any queued MOK enrollment using [Foundation section 6](WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#6-reboot-and-verify).
After reboot, reconnect with the verified ECDSA identity. Require the selected
loaded wolf cohort and positive readiness **before generating a wrapping key or
creating the final pool**:

```sh
set -euo pipefail
test "$(uname -r)" = "$(cat /etc/kernel/proxmox-boot-pin)"
test "$(cat /proc/sys/crypto/fips_enabled)" = 1
grep -Fx ENABLED=no /etc/wolfssl-fips/zfs-root.conf
test "$(zfs get -H -o value encryption "$(findmnt -n -o SOURCE --target /)")" = off
mokutil --sb-state | grep -Fx 'SecureBoot enabled'
wolfssl-fips-zfs-root-prepare --check
if wolfssl-fips-pve-verify; then
  verifier_status=0
else
  verifier_status=$?
fi
printf 'Provisioning-source verifier status: %s\n' "$verifier_status"
```

Require the readiness helper to return zero and report the intended kernel,
wolfZFS/SPL/LinuxKM and provider/private-RNG checks. Review the verifier output:
the disabled plaintext source is not an accepted encrypted root. Only ordinary
unconfigured-storage failures described in Foundation may remain at this stage.
Resolve every kernel, provider, signing, SSH or readiness failure. Use `--login`
separately at an interactive terminal when checking management credentials; its
password prompt reads `/dev/tty`.

## 2. Select the final devices

Set persistent identities for the two empty final boot disks and the dedicated
OSD disk. Review their serial numbers, sizes and source-pool membership before
continuing. The following guards refuse targets with partitions, signatures,
mounts or holders; they do not replace the physical device-identity check.

```sh
set -euo pipefail
set +x
ulimit -c 0
sysctl -w kernel.core_pattern=core.disabled fs.suid_dumpable=0
test -z "$(swapon --noheadings --show=NAME)"
target_a=/dev/disk/by-id/<empty-final-boot-disk-a>
target_b=/dev/disk/by-id/<empty-final-boot-disk-b>
osd=/dev/disk/by-id/<dedicated-nvme-osd>
target_pool=rootpool
target_root=/mnt/new-zfs-root
source_root=$(findmnt -n -o SOURCE --target /)
source_vz=$(findmnt -n -o SOURCE --target /var/lib/vz)
source_pool=${source_root%%/*}
test "$source_pool" != "$target_pool"
test "$(findmnt -n -o FSTYPE --target /)" = zfs
test "$(findmnt -n -o FSTYPE --target /var/lib/vz)" = zfs
test "$source_vz" != "$source_root"
lsblk -d -b -o PATH,SIZE,SERIAL,MODEL "$target_a" "$target_b" "$osd"
zpool status -P "$source_pool"
test ! -e "$target_root"
! zpool list -H -o name | grep -Fx "$target_pool"
install -d -m 0700 /root/root-provisioning
python3 - "$target_a" "$target_b" "$osd" <<'PY'
import json,subprocess,sys
from pathlib import Path
devices=[Path(p) for p in sys.argv[1:]]
assert all(p.is_absolute() and str(p).startswith('/dev/disk/by-id/') for p in devices)
assert len({p.resolve() for p in devices})==3
for path in devices:
    rows=json.loads(subprocess.check_output(['lsblk','-J','-b','-o','PATH,TYPE,SIZE,MOUNTPOINTS',str(path)],text=True))['blockdevices']
    assert len(rows)==1 and rows[0]['type']=='disk'
    assert not rows[0].get('children') and not any(rows[0].get('mountpoints') or [])
    assert not list((Path('/sys/class/block')/path.resolve().name/'holders').iterdir())
    assert not json.loads(subprocess.check_output(['wipefs','--no-act','--json',str(path)],text=True))['signatures']
    assert subprocess.run(['fuser',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==1
print('Empty final devices checked; no device modified')
PY
```

Keep these source-shell variables for subsequent node steps. If reconnecting,
set them again and repeat the public identity and mount checks appropriate to the
current stage. Do not replay empty-device checks over an already created target.

## 3. Partition only the checked final pair

**Warning:** The next commands partition and format the two selected final boot
disks. Confirm their persistent identities immediately before running them.
The reserved first partition follows Proxmox geometry; this profile boots only
through UEFI GRUB. The OSD disk is not an argument to any write command.

```sh
for disk in "$target_a" "$target_b"; do
  sgdisk -a1 -n1:34:2047 -t1:EF02 "$disk"
  sgdisk -n2:2048:+512M -t2:EF00 "$disk"
  sgdisk -n3:0:0 -t3:BF01 "$disk"
  blockdev --rereadpt "$disk"
done
udevadm settle
mkfs.vfat -F 32 -n ROOTESP_A "${target_a}-part2"
mkfs.vfat -F 32 -n ROOTESP_B "${target_b}-part2"
sgdisk --print "$target_a"
sgdisk --print "$target_b"
lsblk -b -o PATH,SIZE,PARTTYPE,FSTYPE,UUID "$target_a" "$target_b" "$osd"
```

Require unique GPTs, two 512MiB VFAT ESPs with distinct UUIDs, and two ZFS members.
Do not rerun this phase over partial state; inspect it first.

## 4. Retain the new wrapping key outside Proxmox

**Management host:** Save the accompanying [key receiver](scripts/receive-root-key.py)
from the authenticated guide bundle. The receiver sends native generator stdout
directly to one exclusive mode0600 file, fsyncs it and its mode0700 directory, and
reports only public status. It never prints, hashes or measures the key. A failed
transfer leaves a quarantined destination; do not reuse that file or its key.

Prepare a root-owned SSH configuration with the ECDSA administrative identity
already authorized by Foundation. Use the verified `known_hosts` established
from this management host during that preparation. Its copied file must contain the
source's ECDSA P-256 host key, independently verified at the node console. The
client identity may be ECDSA P-256, P-384 or P-521. For example:

```sh
install -d -m 0700 /root/zfs-root-provisioning
cat > /root/zfs-root-provisioning/ssh.conf <<'SSH'
Host root-provisioner
    HostName <provisioning-node>
    User root
    IdentityFile /root/.ssh/<authorized-ecdsa-identity>
    IdentitiesOnly yes
    BatchMode yes
    ControlMaster no
    ControlPath none
    ControlPersist no
    StrictHostKeyChecking yes
    UserKnownHostsFile /root/zfs-root-provisioning/known_hosts
    HostKeyAlgorithms ecdsa-sha2-nistp256
    PubkeyAcceptedAlgorithms ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521
SSH
chmod 0600 /root/zfs-root-provisioning/ssh.conf
install -m0600 /root/.ssh/known_hosts /root/zfs-root-provisioning/known_hosts
ssh_config=/root/zfs-root-provisioning/ssh.conf
source_host=root-provisioner
ssh -T -F "$ssh_config" "$source_host" true
```

Keep multiplexing disabled so key generation, transfer and source reconnection
perform fresh SSH host checks. Do not weaken host-key checking to make that
connection succeed. Confirm the
encrypted primary/recovery mounts and distinct backing devices, then create
protected custody directories. Replace the mountpoints and receiver path below.
Neither mount may be the source or target Proxmox filesystem.

```sh
set -euo pipefail
set +x
umask 077
ulimit -c 0
sysctl -w kernel.core_pattern=core.disabled fs.suid_dumpable=0
test -z "$(swapon --noheadings --show=NAME)"
custody_mount=/mnt/<encrypted-primary-custody-volume>
recovery_mount=/mnt/<encrypted-recovery-custody-volume>
mountpoint -q "$custody_mount"
mountpoint -q "$recovery_mount"
test "$(findmnt -n -o MAJ:MIN --target "$custody_mount")" != \
     "$(findmnt -n -o MAJ:MIN --target "$recovery_mount")"
findmnt --target "$custody_mount"
findmnt --target "$recovery_mount"
custody_dir="$custody_mount/zfs-root-keys"
recovery_dir="$recovery_mount/zfs-root-keys"
install -d -o0 -g0 -m0700 "$custody_dir" "$recovery_dir"
key_file="$custody_dir/<new-root-key-file>"
recovery_file="$recovery_dir/<new-root-key-file>"
receiver=/root/<guide-assets>/receive-root-key.py
source_boot_id=$(ssh -T -F "$ssh_config" "$source_host" cat /proc/sys/kernel/random/boot_id)
python3 "$receiver" --ssh-config "$ssh_config" --source "$source_host" \
  --destination "$key_file" --expected-source-boot-id "$source_boot_id"
test ! -e "$recovery_file"
(set -C; cat "$key_file" > "$recovery_file")
chmod 0600 "$recovery_file"
sync -f "$recovery_file"
sync -f "$recovery_dir"
cmp -s "$key_file" "$recovery_file"
```

The mount checks confirm placement and separation, not storage encryption;
independent encryption verification remains a prerequisite. Test access to the
recovery copy through the site's custody process before retiring the source.
Do not print the key, put it in shell arguments/environment, hash it, report its
length or place it on either Proxmox filesystem. Use the retained key only through
protected input. Guest/PBS backups do not contain this root wrapping key.

## 5. Create fresh native pool/dataset keys under the active stack

On the source, save the public creation command below with the same final-device
identities and pool name. It repeats readiness, consumes the key only through
native ZFS stdin, then immediately replaces the temporary stdin keylocation with
`prompt` before any OS copy. New pool and dataset master keys are therefore
created while the intended stack is active; this is not a stock-pool rewrap.

```sh
cat > /root/root-provisioning/create-target-pool.sh <<'SH'
#!/bin/bash
set -euo pipefail
set +x
ulimit -c 0
unset LD_PRELOAD LD_LIBRARY_PATH OPENSSL_CONF_INCLUDE OPENSSL_TRACE
export OPENSSL_CONF=/etc/wolfprovider/openssl.cnf
test "$(cat /proc/sys/kernel/core_pattern)" = core.disabled
test "$(cat /proc/sys/fs/suid_dumpable)" = 0
test -z "$(swapon --noheadings --show=NAME)"
target_a=/dev/disk/by-id/<empty-final-boot-disk-a>
target_b=/dev/disk/by-id/<empty-final-boot-disk-b>
target_pool=rootpool
target_root=/mnt/new-zfs-root
wolfssl-fips-zfs-root-prepare --check </dev/null
test ! -e "$target_root"
! zpool list -H -o name | grep -Fx "$target_pool"
install -d -m 0700 "$target_root"
zpool create -o ashift=12 -o autotrim=on -o cachefile=none \
  -O encryption=aes-256-gcm -O keyformat=hex \
  -O keylocation=file:///dev/stdin \
  -O acltype=posixacl -O xattr=sa -O compression=lz4 -O relatime=on \
  -O canmount=off -O mountpoint=/ -R "$target_root" \
  "$target_pool" mirror "${target_a}-part3" "${target_b}-part3"
zfs set keylocation=prompt "$target_pool" </dev/null
test "$(zfs get -H -o value keylocation "$target_pool")" = prompt
zfs create -o canmount=off -o mountpoint=none "$target_pool/ROOT" </dev/null
zfs create -o canmount=noauto -o mountpoint=/ "$target_pool/ROOT/pve-1" </dev/null
zfs mount "$target_pool/ROOT/pve-1" </dev/null
zfs create -o canmount=on -o mountpoint=/var/lib/vz "$target_pool/var-lib-vz" </dev/null
zpool set "bootfs=$target_pool/ROOT/pve-1" "$target_pool" </dev/null
test "$(findmnt -n -o SOURCE --target "$target_root")" = "$target_pool/ROOT/pve-1"
test "$(findmnt -n -o SOURCE --target "$target_root/var/lib/vz")" = "$target_pool/var-lib-vz"
test "$(zpool get -H -o value cachefile "$target_pool")" = none
for ds in "$target_pool" "$target_pool/ROOT" "$target_pool/ROOT/pve-1" "$target_pool/var-lib-vz"; do
  test "$(zfs get -H -o value encryption "$ds")" = aes-256-gcm
  test "$(zfs get -H -o value encryptionroot "$ds")" = "$target_pool"
done
test "$(zfs get -H -o value keyformat "$target_pool")" = hex
test "$(zfs get -H -o value keylocation "$target_pool")" = prompt
zfs get -r -H -o name,property,value \
  encryption,encryptionroot,keyformat,keylocation,keystatus,canmount,mountpoint "$target_pool"
zpool status -P "$target_pool"
SH
chmod 0700 /root/root-provisioning/create-target-pool.sh
```

From the management host, stream the retained key only into the native creation
command. This is a public pathname redirection, not a key argument or environment
value. Do not log stdin. The source must have no active swap and the host/guest
custody/core guards must remain in effect during private operations.

```sh
ssh -T -F "$ssh_config" -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o ControlMaster=no -o ControlPath=none -o ControlPersist=no \
  -o IdentitiesOnly=yes \
  -o HostKeyAlgorithms=ecdsa-sha2-nistp256 \
  -o PubkeyAcceptedAlgorithms=ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521 \
  "$source_host" 'exec /root/root-provisioning/create-target-pool.sh' < "$key_file"
```

Stop if creation, prompt-property transition or any native property check fails.
Do not start copying with a file keylocation. Preserve the new pool for inspection
instead of recreating it blindly. Require creation to precede OS copy and follow successful readiness.

## 6. Quiesce and snapshot the fresh source

Before stopping pmxcfs, establish temporary source-only fallback access using the
same public administrator authorization. The new source session and copied file
contain public authorized keys, not a private SSH key. This temporary override and
public file are excluded from the final-root copy.

```sh
install -m 0600 /etc/pve/priv/authorized_keys \
  /root/.ssh/root-provisioning_authorized_keys
cat > /etc/ssh/sshd_config.d/00-root-provisioning-access.conf <<'SSH'
AuthorizedKeysFile .ssh/authorized_keys .ssh/root-provisioning_authorized_keys
SSH
sshd -t
sshd -T | grep '^authorizedkeysfile '
systemctl reload ssh
test ! -e /etc/pve/corosync.conf
test -z "$(find -L /etc/pve/qemu-server /etc/pve/lxc -maxdepth 1 -name '*.conf' -print)"
pvesm set local-zfs --disable 1
pvesh get /storage/local-zfs --output-format json
systemctl stop apt-daily.timer apt-daily-upgrade.timer
! pgrep -x 'apt|apt-get|dpkg|unattended-upgr'
for unit in pveproxy pvedaemon pvestatd pvescheduler pve-cluster; do
  systemctl stop "$unit"
done
test "$(systemctl is-active pve-cluster || true)" = inactive
! findmnt -n --mountpoint /etc/pve
```

Open a separate fresh strict-ECDSA SSH connection from management now, using the
same identity. Do not proceed on the strength of an existing session alone.
Confirm the source pool/root and final staging mounts remain correct. Any
unexpected separately mounted filesystem or populated installer container needs
review before continuing; this path copies exactly root plus `/var/lib/vz`.

```sh
test "$(findmnt -n -o SOURCE --target /)" = "$source_root"
test "$(findmnt -n -o SOURCE --target /var/lib/vz)" = "$source_vz"
test "$(findmnt -n -o SOURCE --target "$target_root")" = "$target_pool/ROOT/pve-1"
test "$(findmnt -n -o SOURCE --target "$target_root/var/lib/vz")" = "$target_pool/var-lib-vz"
findmnt -rn -t zfs -o SOURCE,TARGET
snapshot=root-provision-copy
! zfs list -H -t snapshot -o name | grep -F "@$snapshot"
sync
zfs snapshot "$source_root@$snapshot" "$source_vz@$snapshot"
rsync -aHAX --numeric-ids --one-file-system \
  --exclude=/.zfs/ --exclude=/dev/* --exclude=/proc/* --exclude=/sys/* \
  --exclude=/run/* --exclude=/tmp/* --exclude=/var/tmp/* \
  --exclude=/mnt/* --exclude=/media/* --exclude=/var/lib/vz/* \
  --exclude=/etc/zfs/zpool.cache --exclude=/etc/zfs/zfs-list.cache/ \
  --exclude=/etc/kernel/proxmox-boot-uuids \
  --exclude=/etc/kernel/proxmox-boot-pin --exclude=/etc/kernel/next-boot-pin \
  --exclude=/etc/default/grub.d/proxmox-kernel-pin.cfg \
  --exclude=/boot/grub/grub.cfg --exclude=/boot/grub/grubenv \
  --exclude=/var/lib/wolfssl-fips-zfs-root/ \
  --exclude=/root/root-provisioning/ \
  --exclude=/root/.ssh/root-provisioning_authorized_keys \
  --exclude=/etc/ssh/sshd_config.d/00-root-provisioning-access.conf \
  "/.zfs/snapshot/$snapshot/" "$target_root/"
rsync -aHAX --numeric-ids --one-file-system --exclude=/.zfs/ \
  "/var/lib/vz/.zfs/snapshot/$snapshot/" "$target_root/var/lib/vz/"
test -f "$target_root/var/lib/pve-cluster/config.db"
test -d "$target_root/etc/pve"
test ! -e "$target_root/etc/zfs/zpool.cache"
test ! -e "$target_root/etc/kernel/proxmox-boot-pin"
test ! -e "$target_root/etc/kernel/next-boot-pin"
test ! -e "$target_root/etc/default/grub.d/proxmox-kernel-pin.cfg"
test ! -e "$target_root/var/lib/wolfssl-fips-zfs-root"
cmp /etc/hostid "$target_root/etc/hostid"
```

Do not add `--delete`, copy from a changing live source, or use ZFS send/receive
to preserve the old unencrypted root's dataset/key history. ACL/xattr/hardlink and
numeric-owner preservation is part of this fresh file-copy path.

## 7. Configure target-only boot inputs and disk-backed /tmp

The following bounded rewrite touches only named files under the target. It
preserves source/node hostid and shipping FIPS defaults. It refuses unexpected
fstab mounts instead of hiding a missing source/target ownership decision.

Use the site's controlled management console for the native attended prompt.
Configure any required console routing using normal Proxmox boot settings; do
not record private prompt input.

```sh
python3 - "$target_root" "$source_root" "$target_pool" <<'PY'
import os,re,shlex,sys
from pathlib import Path
t=Path(sys.argv[1]);source=sys.argv[2];pool=sys.argv[3];root=pool+'/ROOT/pve-1'
assert t.is_absolute() and t.resolve()==t and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]*',pool)
def path(name):
    p=t/name
    assert p.resolve().is_relative_to(t) and not p.is_symlink()
    return p
fstab=path('etc/fstab').read_text()
for line in fstab.splitlines():
    fields=line.split()
    if fields and not fields[0].startswith('#'):
        assert len(fields)>=4 and fields[0]=='proc' and fields[1]=='/proc' and fields[2]=='proc','Review target fstab entry before proceeding'
for p in [path('etc/default/grub'),*sorted(path('etc/default/grub.d').glob('*.cfg'))]:
    assert p.resolve().is_relative_to(t) and not p.is_symlink()
    lines=[]
    for line in p.read_text().splitlines(keepends=True):
        if not line.lstrip().startswith('#'):
            line=re.sub(r'root=ZFS='+re.escape(source)+r'(?=[\s\x22\x27]|$)','root=ZFS='+root,line)
        lines.append(line)
    text=''.join(lines)
    active='\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
    assert not re.search(r'fips=0(?=[\s\x22\x27]|$)',active)
    assert all(token=='root=ZFS='+root for token in re.findall(r'root=[^\s\x22\x27]+',active))
    p.write_text(text)
cmd=path('etc/kernel/cmdline')
tokens=shlex.split(cmd.read_text()) if cmd.exists() else []
assert all(token=='fips=1' for token in tokens if token.startswith('fips='))
tokens=['root=ZFS='+root if token.startswith('root=') else token for token in tokens]
if not any(token.startswith('root=') for token in tokens):tokens.append('root=ZFS='+root)
if 'boot=zfs' not in tokens:tokens.append('boot=zfs')
if 'fips=1' not in tokens:tokens.append('fips=1')
cmd.write_text(' '.join(tokens)+'\n')
path('etc/default/grub.d/zz-wolfssl-zfs-root.cfg').write_text('GRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX root=ZFS='+root+' boot=zfs'+'"\nGRUB_DISABLE_OS_PROBER=true\n')
path('etc/initramfs-tools/conf.d/zz-wolfssl-zfs-root').write_text('ROOT=ZFS='+root+'\nMODULES=most\n')
path('etc/kernel/proxmox-boot-uuids').write_text('')
policy=path('etc/wolfssl-fips/zfs-root.conf')
policy.write_text('ENABLED=yes\nROOT_DATASET='+root+'\nENCRYPTION_ROOT='+pool+'\nKEYFORMAT=hex\nKEYLOCATION=prompt\n')
policy.chmod(0o600)
for relative in ['dev','proc','sys','run','mnt','media']:
    path(relative).mkdir(parents=True,exist_ok=True)
for relative in ['tmp','var/tmp']:
    p=path(relative);p.mkdir(parents=True,exist_ok=True);p.chmod(0o1777)
print('TARGET_ONLY_ROOT_POLICY_AND_BOOT_INPUTS_SET')
PY
systemctl --root="$target_root" mask tmp.mount
test "$(stat -c %a "$target_root/tmp")" = 1777
test "$(stat -c %a "$target_root/var/tmp")" = 1777
grep -Fx ENABLED=no /etc/wolfssl-fips/zfs-root.conf
```

Preserve the shipping `GRUB_CMDLINE_LINUX_DEFAULT` FIPS settings. Masking
`tmp.mount` keeps target `/tmp` on encrypted root; it does not change the source.
The target has neither source ESPs nor source pin state. Generated
GRUB output is rebuilt from target inputs; no old `grubenv` selection is copied.

## 8. Bootstrap the target in a private mount namespace

Run this phase in one new private mount-namespace shell. Export the public
variables explicitly. Binds are rslave and target `/run` is a separate tmpfs.
On error, stop for inspection; do not force-unmount or export a held pool.

```sh
export target_a target_b target_pool target_root source_root source_pool
unshare --mount --propagation private /bin/bash
set -euo pipefail
for name in dev proc sys; do
  mount --rbind "/$name" "$target_root/$name"
  mount --make-rslave "$target_root/$name"
done
mount -t tmpfs -o mode=0755,nosuid,nodev target-root-run "$target_root/run"
install -d -o0 -g0 -m1777 "$target_root/run/lock"
install -d -m 0755 "$target_root/run/udev"
mount --bind /run/udev "$target_root/run/udev"
mount --make-rslave "$target_root/run/udev"
mount -o remount,bind,ro "$target_root/run/udev"
findmnt -n -o OPTIONS --mountpoint "$target_root/run/udev" \
  | tr ',' '\n' | grep -Fx ro
test ! -e "$target_root/usr/sbin/policy-rc.d"
cat > "$target_root/usr/sbin/policy-rc.d" <<'SH'
#!/bin/sh
exit 101
SH
chmod 0755 "$target_root/usr/sbin/policy-rc.d"
chroot "$target_root" findmnt -n -o SOURCE,FSTYPE --target /
chroot "$target_root" findmnt -n -o SOURCE,FSTYPE --target /var/lib/vz
chroot "$target_root" cat /proc/cmdline
test "$(chroot "$target_root" findmnt -n -o SOURCE --target /)" = "$target_pool/ROOT/pve-1"
test "$(chroot "$target_root" findmnt -n -o SOURCE --target /var/lib/vz)" = "$target_pool/var-lib-vz"
test "$(chroot "$target_root" stat -f -c %T /)" = zfs
chroot "$target_root" grub-mkrelpath /
expected_devices=$(readlink -e "${target_a}-part3" "${target_b}-part3" | sort)
device_output=$(chroot "$target_root" grub-probe --target=device /)
actual_devices=$(for device in $device_output; do readlink -e "$device"; done | sort)
test "$actual_devices" = "$expected_devices"
test "$(blkid -s UUID -o value "${target_a}-part2")" != \
     "$(blkid -s UUID -o value "${target_b}-part2")"
for esp in "${target_a}-part2" "${target_b}-part2"; do
  test "$(blockdev --getsize64 "$esp")" = 536870912
  test "$(lsblk --noheadings --nodeps -o FSTYPE "$esp")" = vfat
  test "$(lsblk --noheadings --nodeps -o PARTTYPE "$esp")" = c12a7328-f81f-11d2-ba4b-00a0c93ec93b
  test "$(lsblk --pairs --bytes --nodeps --noheadings -o UUID,SIZE,FSTYPE,PARTTYPE "$esp")" = \
       "$(chroot "$target_root" lsblk --pairs --bytes --nodeps --noheadings -o UUID,SIZE,FSTYPE,PARTTYPE "$esp")"
  test "$(blkid -s UUID -o value "$esp")" = \
       "$(chroot "$target_root" blkid -s UUID -o value "$esp")"
done
test ! -e "$target_root/run/wolfssl-fips/zfs-root/early-receipt"
test ! -s "$target_root/etc/kernel/proxmox-boot-uuids"
selected_kernel=$(uname -r)
chroot "$target_root" update-initramfs -u -k "$selected_kernel"
test ! -s "$target_root/etc/kernel/proxmox-boot-uuids"
chroot "$target_root" proxmox-boot-tool init "${target_a}-part2" grub
chroot "$target_root" proxmox-boot-tool init "${target_b}-part2" grub
chroot "$target_root" /usr/lib/wolfssl-fips-zfs-root/update-cohort --probe
chroot "$target_root" /usr/lib/wolfssl-fips-zfs-root/update-cohort
chroot "$target_root" proxmox-boot-tool status
chroot "$target_root" proxmox-boot-tool kernel list
test "$(cat "$target_root/etc/kernel/proxmox-boot-pin")" = "$selected_kernel"
```

Check the chroot results before the build. `/proc/cmdline` remains the
source boot's public command line; no target early receipt exists yet. Require
actual target filesystem resolution, successful grub-mkrelpath and both exact
final leaves from grub-probe. Stock GRUB's fs(/) probe can return1 with the known
`unknown filesystem` diagnostic for encrypted root; this does not establish a
failure of the native mounted ZFS filesystem. The stock grub-mkconfig path
tolerates that diagnostic while Proxmox binds each public FAT ESP on `/boot`
to prepare boot files. Require the separate device, ESP and final artifact checks.
Stop
if the chroot resolves the source or unexpected disks. Do not manufacture a
receipt or call `--accept-boot` during staging.

The read-only, rslave `/run/udev` bind supplies the source's public device database
inside the otherwise separate target `/run`; verify matching lsblk/blkid metadata
before PBT. Recursive `/run` unmount later removes this bind too. GRUB reads public
boot artifacts on the ESPs; native ZFS unlock occurs later.

The separate `/run` must contain an empty root-owned mode1777 `/run/lock` before
package maintenance or cohort activation. Do not reuse the source runtime directory.

The direct initial initramfs build precedes ESP init. The normal
updater refreshes both ESPs, validates flat public `/vmlinuz-*` and `/initrd.img-*`
paths, coherent payloads, effective target roots and persistent GRUB selection,
then pins and explicitly refreshes again. Real generated GRUB uses tabs and may
contain agreeing duplicate root arguments; do not normalize it into a different
format for acceptance. No source root argument may survive.

Before leaving the namespace, remove only the exact task-created service-start
inhibitor, sync and cleanly unmount the target binds. Do not change the source
policy, source cache or source pin/ESP files.

```sh
python3 - "$target_root/usr/sbin/policy-rc.d" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);assert not p.is_symlink()
assert p.read_text()=='#!/bin/sh\nexit 101\n'
p.unlink()
PY
sync
for name in run sys proc dev; do
  umount --recursive "$target_root/$name"
done
exit
```

## 9. Cleanly export and remove source from the boot topology

Back in the outer source shell, verify the source policy and clean target state,
then export only the target pool. Failed unmount/export requires inspection.

```sh
grep -Fx ENABLED=no /etc/wolfssl-fips/zfs-root.conf
test "$(zfs get -H -o value keylocation "$target_pool")" = prompt
test "$(zpool status -x "$target_pool")" = "pool '$target_pool' is healthy"
sync
zfs unmount "$target_pool/var-lib-vz"
zfs unmount "$target_pool/ROOT/pve-1"
zpool export "$target_pool"
! zpool list -H -o name | grep -Fx "$target_pool"
poweroff
```

After ordinary shutdown, physically exclude both temporary source disks before
the first final-root boot. Retain them offline as provisioning recovery until
target acceptance. Boot through the final managed GRUB ESPs. Submit the retained
key only through the native attended prompt using the site's controlled secret-entry
process. Keep input and private session output out of terminal recordings and logs.

## 10. Accept the actual target root before Ceph configuration

On the final target, set the selected public pool name again. Require all root
guards, current-boot early receipt, independent actual-root checks, required
encrypted backing paths and both managed ESPs. An SSH connection alone is not an
accepted boot.

The receipt and cohort registry are mutable public metadata, not measured-boot or
loaded-code attestation. Verification also checks the actual mounted root and
its native encryption properties independently.

```sh
set -euo pipefail
target_pool=rootpool
test "$(findmnt -n -o SOURCE --target /)" = "$target_pool/ROOT/pve-1"
test "$(findmnt -n -o SOURCE --target /tmp)" = "$target_pool/ROOT/pve-1"
test "$(findmnt -n -o SOURCE --target /var/tmp)" = "$target_pool/ROOT/pve-1"
test "$(findmnt -n -o SOURCE --target /var/lib/vz)" = "$target_pool/var-lib-vz"
test "$(zfs get -H -o value encryption "$target_pool/ROOT/pve-1")" = aes-256-gcm
test "$(zfs get -H -o value encryptionroot "$target_pool/ROOT/pve-1")" = "$target_pool"
test "$(zfs get -H -o value keyformat "$target_pool")" = hex
test "$(zfs get -H -o value keylocation "$target_pool")" = prompt
test "$(cat /proc/sys/crypto/fips_enabled)" = 1
mokutil --sb-state | grep -Fx 'SecureBoot enabled'
test "$(systemctl show -p Result --value wolfssl-fips-zfs-root-boot.service)" = success
test "$(systemctl show -p ExecMainCode --value wolfssl-fips-zfs-root-boot.service)" = 1
test "$(systemctl show -p ExecMainStatus --value wolfssl-fips-zfs-root-boot.service)" = 0
systemctl show -p Result -p ExecMainCode -p ExecMainStatus \
  -p ExecMainStartTimestampMonotonic wolfssl-fips-zfs-root-boot.service
journalctl -b -u wolfssl-fips-zfs-root-boot.service --no-pager
/usr/lib/wolfssl-fips-pve-config/zfs-root-check
proxmox-boot-tool status
zpool status -P "$target_pool"
if wolfssl-fips-pve-verify; then
  verifier_status=0
else
  verifier_status=$?
fi
printf 'Full verifier native status: %s\n' "$verifier_status"
```

Require the root-specific checker to succeed. Review every full-verifier result
before the next block; only the known ordinary not-yet-configured storage failures
may be deferred. No root, early receipt, backing-path, provider or module failure
is deferred. The direct root checker runs the same host-root checks as the full verifier; it
does not substitute for reviewing the remaining service and storage results.

```sh
printf '%s\n' encrypted-root-public-persistence-test > /var/lib/root-provisioning-public-data
printf '%s\n' encrypted-vz-public-persistence-test > /var/lib/vz/root-provisioning-public-data
sync
reboot
```

After a second attended boot, repeat the actual root/boot checks and verify both
public test files:

```sh
grep -Fx encrypted-root-public-persistence-test /var/lib/root-provisioning-public-data
grep -Fx encrypted-vz-public-persistence-test /var/lib/vz/root-provisioning-public-data
/usr/lib/wolfssl-fips-pve-config/zfs-root-check
```

 Only ordinary not-yet-configured storage failures identified
by Foundation may be deferred; any host-root failure blocks acceptance. Once
accepted, continue the existing new-cluster/Ceph/LUKS/Backup procedures. Root
preparation has not touched the NVMe. Do not rerun stock `pveceph install` after
the wolfSSL repository is configured. Use the existing Ceph checks for messenger mode, encrypted OSDs, guest data and
backup placement after Ceph is configured. Root encryption does not establish
encryption for every guest volume or external backup staging directory.

The acceptance service is a oneshot without `RemainAfterExit`; inactive after
successful completion is expected. Require its successful native execution in
this boot and the independently checked current root, not an active unit state.

## 11. Maintain an accepted encrypted-root node

Perform updates only with a healthy mirror, clean package state, both ESPs
available, retained wrapping/recovery keys and usable console access. Resolve
every root-check failure before changing packages. Use only a coherent root-aware
evaluation cohort; do not bypass its dependencies with a stock kernel or the
conflicting guest-ZFS metapackage.

The exercised update procedure covers reviewed root-integration and common
configuration changes within the same kernel cohort. A transition to a different
kernel, module or provider requires a separately qualified vendor cohort and
successful encrypted-root boot test before use. These instructions do not
qualify arbitrary future kernel updates or direct boot from a saved archive.

```sh
set -euo pipefail
target_pool=rootpool
test -z "$(dpkg --audit)"
test "$(zpool status -x "$target_pool")" = "pool '$target_pool' is healthy"
/usr/lib/wolfssl-fips-pve-config/zfs-root-check
proxmox-boot-tool status
```

Follow [Node Maintenance](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#1-update-the-installed-packages)
for preferences, maintenance windows and transaction review, using only updates
approved for this experimental cohort. For evaluation-bundle updates, authenticate
the new delivery and repeat section 1's manifest/package-selection/APT commands;
do not rerun its disabled-source policy or provisioning probe checks on active root.

After APT completes, run the normal updater explicitly, including after a
common-config-only upgrade. The root file triggers do not watch the common
checker path; until the recorded package inventory is refreshed, strict root
checking correctly reports that its installed package changed.

```sh
test -z "$(dpkg --audit)"
/usr/lib/wolfssl-fips-zfs-root/update-cohort
/usr/lib/wolfssl-fips-pve-config/zfs-root-check
proxmox-boot-tool status
reboot
```

Require the updater to validate both ESPs and pin the intended cohort. Reboot
through the attended prompt, then repeat section 10 and the Ceph deployment's
health/data checks before returning the node to service. A successful oneshot
boot service is normally inactive afterward; inspect its native result rather
than requiring an active service state.

If a root-package configuration or initramfs build fails, retain the error and
inspect the package, prior boot selection and ESP state before rebooting. Correct
the reported cause and resume ordinary configuration:

```sh
dpkg --audit
dpkg --configure wolfssl-fips-pve-zfs-root
/usr/lib/wolfssl-fips-zfs-root/update-cohort
/usr/lib/wolfssl-fips-pve-config/zfs-root-check
```

Do not force package state, disable root policy, remove the protected root package
or select a stock kernel to bypass a rejected cohort. Keep the accepted recovery
kernel/initramfs/cohort and matching signed module packages; do not delete their
state manually. The ordinary guide's manual kernel-pin and unused-Foundation
stock-return procedures do not apply to active encrypted root.

## 12. Preserve recovery access and check the mirror

Retain the external wrapping key and independently accessible recovery copy,
known-working signed boot artifacts and compatible recovery media. Loss of the
wrapping key cannot be repaired by a package reinstall or guest backup. Keep the
temporary source offline until the final root, reboot, storage and recovery checks
are accepted. Do not assume an unencrypted source is an up-to-date data backup.

At an unlock failure, use the controlled console and preserve the public error.
For failed-boot repair, use compatible provisioning media and contact wolfSSL;
retaining an accepted archive does not provide an arbitrary older-cohort restore
procedure.
Do not repeatedly rebuild or replace a pool, change key format or introduce a key
file into an ESP/initramfs. An unavailable mirror member or nonzero pool counter
requires diagnosis before acceptance. Preserve `zpool status -P` and validate
recovered data; do not automatically clear counters or treat a successful boot
as proof that recovery completed.

Preserve import-time checksum reports separately from the resilver, scrub and
data-readback results. Compare diagnosed reporting behavior with the matching
stock ZFS baseline; stock-equivalent reporting alone does not require a wolfSSL
or upstream ZFS code change. Unexpected errors and known data errors still need
investigation. The strict root checker continues to report degraded health or
nonzero vdev counters as failures. Record those failures; do not suppress them
or describe functional recovery as complete healthy-node acceptance.

### 12.1 Return an original mirror member

If one original boot disk is unavailable, boot the surviving disk's managed
UEFI GRUB entry and attend the normal key prompt. Inspect the root checks and pool
before performing recovery:

```sh
target_pool=rootpool
if /usr/lib/wolfssl-fips-pve-config/zfs-root-check; then
  root_status=0
else
  root_status=$?
fi
printf 'Root checker status during recovery: %s\n' "$root_status"
zpool status -P "$target_pool"
proxmox-boot-tool status
```

The missing mirror member and its ESP remain failed health checks. Require every
root-policy/current-receipt, kernel/FIPS, installed/loaded-cohort and encrypted
host-path group to pass. The surviving member must be ONLINE with zero read,
write and checksum counters and no known data errors. Resolve any other failure;
this degraded state is not complete node acceptance. Do not update packages,
change the kernel pin or rebuild ESPs while the mirror is degraded.
The boot-acceptance unit also remains failed during the degraded test; require
its successful native result again after the final healthy boot.

To return the **same unmodified original member**, shut down normally, verify its
persistent identity and reconnect it before booting with both disks. Do not wipe,
repartition, replace or reinitialize that member or its ESP. New-disk replacement
is a separate repair procedure and is not covered here.

After native unlock, wait for automatic resilver, inspect its result, then scrub:

```sh
target_pool=rootpool
zpool wait -t resilver "$target_pool"
zpool status -P "$target_pool"
zpool scrub "$target_pool"
zpool wait -t scrub "$target_pool"
zpool status -P "$target_pool"
/usr/lib/wolfssl-fips-pve-config/zfs-root-check
```

Require the mirror and both members ONLINE, completed recovery/scrub with zero
errors, zero vdev counters, no known data errors and both original ESPs matching
the accepted cohort. Check known data written while the member was absent.
Preserve and investigate any error; never clear counters to make these checks
pass. Resilvering ZFS does not repair or synchronize an ESP independently.

### 12.2 Verify the recovered member on a disposable evaluation node

Before relying on recovery, test it without production guests or data. While one
original member is absent and the surviving member passes the crypto/path checks
above, create new public markers on root and host data. Record the printed marker
outside the node; it is test data, not a wrapping key.

```sh
marker="public-mirror-return-$(cat /proc/sys/kernel/random/uuid)"
printf '%s\n' "$marker" > /var/lib/root-provisioning-recovery-data
printf '%s\n' "$marker" > /var/lib/vz/root-provisioning-recovery-data
sync -f /var/lib/root-provisioning-recovery-data
sync -f /var/lib/vz/root-provisioning-recovery-data
printf '%s\n' "$marker"
```

Return the absent original member and complete section 12.1. Then shut down
normally and temporarily disconnect the member that supplied those writes, leaving
only the recovered member and the separate OSD disks. Boot its normal managed
GRUB entry, unlock and repeat the degraded crypto/path inspection. Verify both
markers against the value recorded while the member was absent:

```sh
expected_marker='<recorded-public-marker>'
grep -Fx -- "$expected_marker" /var/lib/root-provisioning-recovery-data
grep -Fx -- "$expected_marker" /var/lib/vz/root-provisioning-recovery-data
```

If Ceph is configured, also check its encrypted OSD activation and known data as
described in the Ceph guide. This readback tests the recovered member; it does not
make a degraded node healthy or establish Ceph redundancy/HA.

Shut down normally, reconnect both original disks and repeat section 12.1's
resilver/scrub and strict root checks. Repeat full verification and the configured
storage health/data checks. Require zero `FAIL` results, assess every warning and
retain both ESPs before returning the node to service.

Use the [Backup guide](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md) for independent
guest/backup recovery and retained backup encryption keys. Those keys are distinct
from the root wrapping key. Configured encrypted-root nodes return to stock by
reinstallation, with independently restored data and keys.
