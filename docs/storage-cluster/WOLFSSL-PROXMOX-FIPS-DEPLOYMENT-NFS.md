# wolfSSL FIPS for Proxmox VE — NFS Storage

Use the Linux NFSv4.2/TCP client with Kerberos privacy (`sec=krb5p`) for VM storage
and file backups.
Run commands as root on the Proxmox node; substitute site values for placeholders.

`krb5p` protects file data in transit. The filer must provide server cryptography and
at-rest encryption for VM disks and backup archives. Container migration, backup and
restore require separate qualification.

## 1. Prepare every client

Complete [Installation](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md) on each node,
selecting `wolfssl-fips-pve-nfs` before its reboot. For a cluster, complete
[Cluster configuration](WOLFSSL-PROXMOX-FIPS-CLUSTER.md) before adding shared storage.

The package checks cryptographic prerequisites before `rpc.gssd` starts.

Arrange with the realm and filer administrators:

- A canonical server FQDN matching its NFS service principal, consistent name resolution
  and clocks, and compatible AES session-key enctypes.
- A node-specific `/etc/krb5.keytab`, owned by root with mode `0600`, and correct realm
  and NFS idmap settings. Preserve existing site configuration.
- An export restricted to `krb5p`, with identity mapping and permissions authorizing
  Proxmox root file operations.
- Filer encryption and recovery meeting the site's at-rest requirements.

Prepare every assigned node before enabling storage. Shared definitions distribute neither
keytabs nor node-local settings; unrestricted definitions apply to every cluster member.

Then start `rpc.gssd` on every assigned node:

```sh
systemctl start rpc-gssd
```

## 2. Add the shared storage

Register once using an unused `<nfs-storage>` ID, the canonical `<server-fqdn>` and
authorized `<export-path>`:

```sh
pvesm add nfs <nfs-storage> --server <server-fqdn> --export <export-path> \
  --content images,backup --options vers=4.2,proto=tcp,sec=krb5p
```

Proxmox mounts NFS automatically; no `/etc/fstab` entry is needed. Other options follow the
[Proxmox NFS backend](https://pve.proxmox.com/pve-docs/chapter-pvesm.html#storage_nfs).

## 3. Verify access on every assigned node

```sh
wolfssl-fips-pve-nfs-verify <nfs-storage>
```

Require all NFS integration and mount checks to pass for the intended server/export.
Stop on failure; the general host verifier does not replace this check.

On the verified mount, compare a flushed write with a direct read:

```sh
set -o pipefail
nfs_check_file=$(mktemp /mnt/pve/<nfs-storage>/.nfs-check.XXXXXX)
dd if=/dev/urandom of="$nfs_check_file" bs=1M count=1 conv=fsync status=none
sha256sum "$nfs_check_file"
dd if="$nfs_check_file" bs=1M iflag=direct status=none | sha256sum
rm -- "$nfs_check_file"
```

Require matching digests and successful commands before placing guests or accepting migrations.

## 4. Verify boot, guest placement and recovery

Use a test VM with known data. Check all disk, EFI and TPM-state storage IDs:

```sh
qm config <vmid>
```

Record the boot ID and reboot during maintenance. For clusters, follow
[Cluster maintenance](WOLFSSL-PROXMOX-FIPS-CLUSTER.md#5-maintain-and-restart-the-cluster).

```sh
cat /proc/sys/kernel/random/boot_id
reboot
```

After reconnecting, repeat `cat /proc/sys/kernel/random/boot_id`; require a changed ID
and automatic NFS access with machine credentials. Repeat section 3, confirm onboot
guests start and verify the VM's data.
Complete [Migration acceptance](../_migration/WOLFSSL-PROXMOX-FIPS-MIGRATION-ACCEPTANCE.md)
and a [backup/restore test](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md).

Keep `wolfssl-fips-pve-nfs` installed; follow
[Node maintenance](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md). Quiesce guests,
backup jobs and other export I/O before credential or service changes. Correct failed
prerequisites, keep the startup check enabled and reboot for fresh contexts.

For incorrect-mount-option errors, check the server name/principal, clocks, keytab access,
export authorization and provider/kernel activation. Inspect the daemon log:

```sh
journalctl -b -u rpc-gssd
```

Follow [provider recovery](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#42-provider-cannot-load)
if needed. Repeat section 3 before returning guests to service.
