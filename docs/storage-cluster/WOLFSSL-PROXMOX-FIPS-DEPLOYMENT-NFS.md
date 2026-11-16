# wolfSSL FIPS for Proxmox VE — NFS Storage

Use the Linux NFSv4.2 client over TCP with Kerberos privacy (`sec=krb5p`) for VM storage
and file backups.
Run commands as root on the Proxmox node; replace angle-bracket placeholders with site values.

`krb5p` protects file data in transit, not VM disks or backup archives at rest. Server
cryptography and at-rest encryption remain the filer's responsibility. Container migration,
backup and restore require separate qualification.

## 1. Prepare every client

Complete [Installation](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md) on each node,
selecting `wolfssl-fips-pve-nfs` before its reboot. For a cluster, complete
[Cluster configuration](WOLFSSL-PROXMOX-FIPS-CLUSTER.md) before adding shared storage.

The package supplies the Kerberos backend and `rpc.gssd` provider configuration and
checks kernel prerequisites before each daemon start. Kerberos context setup uses
wolfProvider; file I/O uses kernel RPCSEC_GSS backed by wolfCrypt.

Arrange the following with the realm and filer administrators:

- A canonical server FQDN matching its NFS service principal, consistent name resolution
  and clocks, and compatible AES session-key enctypes.
- A node-specific `/etc/krb5.keytab`, owned by root with mode `0600`, and correct realm
  and NFS idmap settings on every client. Preserve the site's existing configuration.
- An export restricted to `krb5p`, with identity mapping and permissions authorizing the
  Proxmox nodes' required root file operations.
- Filer encryption and recovery arrangements that meet the site's at-rest requirements.

Keytabs authenticate machines; export permissions and identity mapping authorize access.
Shared storage configuration distributes neither keytabs nor node-local settings.
Prepare every assigned member before enabling storage; unrestricted definitions apply
to every cluster member.

After provisioning the credentials and settings, start the daemon on every node:

```sh
systemctl start rpc-gssd
```

## 2. Add the shared storage

Choose an unused `<nfs-storage>` ID, the canonical `<server-fqdn>` and authorized
`<export-path>`. Add the definition once:

```sh
pvesm add nfs <nfs-storage> --server <server-fqdn> --export <export-path> \
  --content images,backup --options vers=4.2,proto=tcp,sec=krb5p
```

Proxmox mounts this backend automatically; no `/etc/fstab` entry is needed. See the
[Proxmox NFS backend](https://pve.proxmox.com/pve-docs/chapter-pvesm.html#storage_nfs)
for ordinary storage options.

## 3. Verify access on every assigned node

```sh
wolfssl-fips-pve-nfs-verify <nfs-storage>
```

Require all checks to pass for the intended server and export. The read-only verifier
checks the active Kerberos, provider and kernel integration, and the NFSv4.2/TCP/`krb5p`
mount. Stop on failure; the general host verifier alone does not establish NFS readiness.

Create a temporary file on the verified mount, flush it, and compare a direct read:

```sh
set -o pipefail
nfs_check_file=$(mktemp /mnt/pve/<nfs-storage>/.nfs-check.XXXXXX)
dd if=/dev/urandom of="$nfs_check_file" bs=1M count=1 conv=fsync status=none
sha256sum "$nfs_check_file"
dd if="$nfs_check_file" bs=1M iflag=direct status=none | sha256sum
rm -- "$nfs_check_file"
```

Require matching digests and successful commands on every node before placing guests
or accepting migrations.

## 4. Verify boot, guest placement and recovery

Use a test VM with known data. Check every disk, EFI and TPM-state storage ID:

```sh
qm config <vmid>
```

In a maintenance window, record the node's boot ID and reboot. On a cluster, follow
[Cluster maintenance](WOLFSSL-PROXMOX-FIPS-CLUSTER.md#5-maintain-and-restart-the-cluster).

```sh
cat /proc/sys/kernel/random/boot_id
reboot
```

After reconnecting, repeat `cat /proc/sys/kernel/random/boot_id` and require a changed ID,
automatic NFS access with machine credentials and section 3's checks. Confirm onboot
guests start and verify the test VM's data.
Complete [Migration acceptance](../_migration/WOLFSSL-PROXMOX-FIPS-MIGRATION-ACCEPTANCE.md)
and a [backup/restore test](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md).

Keep `wolfssl-fips-pve-nfs` installed during updates; follow
[Node maintenance](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md). Quiesce guests,
backup jobs and other export I/O before credential or service changes. Restore failed
prerequisites, keep the startup check enabled, then reboot to establish fresh contexts.

Authentication or provider failures can appear as an incorrect-mount-option error.
Check the server name/principal, clocks, keytab access, export authorization and
provider/kernel activation; inspect the daemon log:

```sh
journalctl -b -u rpc-gssd
```

Follow [provider recovery](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#42-provider-cannot-load)
if needed. Repeat mount and data checks before returning guests to service.
