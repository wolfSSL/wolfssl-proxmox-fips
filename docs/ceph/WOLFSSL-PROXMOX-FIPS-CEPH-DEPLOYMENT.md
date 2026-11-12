# wolfSSL FIPS for Ceph on Proxmox VE — Deployment

Deploy a new Ceph Tentacle cluster on freshly prepared Proxmox VE nodes. This guide covers the
wolfSSL package selection, encrypted OSDs and encrypted Ceph connections. Use Proxmox's
[Ceph administration chapter](https://pve.proxmox.com/pve-docs/chapter-pveceph.html) for topology,
capacity, pools, monitors and routine administration.

Run commands as root. Replace angle-bracket placeholders with the site's devices, storage IDs
and network values. Read [Cluster planning](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md#1-plan-the-cluster)
before preparing the first node.

## 1. Prepare the Ceph packages

Use fresh Proxmox VE 9 nodes, with separate OSD disks and no existing Ceph cluster. This
deployment requires Ceph Tentacle 20.2.5. Install Proxmox's Tentacle packages on each node
**before** adding the wolfSSL repository or installing the Foundation packages. The following
selects the no-subscription repository; use `enterprise` instead when the nodes have the
required subscriptions.

```sh
pveceph install --repository no-subscription --version tentacle
ceph --version
```

Confirm that `ceph --version` reports Tentacle 20.2.5 before continuing. Do not initialize Ceph
yet. Squid is outside this deployment's scope. Do not run `pveceph install` again after
configuring the wolfSSL repository; select the Ceph metapackage during Foundation instead.

The Ceph and ZFS metapackages conflict. Select Ceph for these nodes; encrypted ZFS on the same
node is outside this guide's wolfCrypt storage coverage. Separate local LUKS volumes are
supported with the additional [LUKS metapackage and configuration](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-LUKS.md).

## 2. Install and activate the wolfSSL integration

Complete [Foundation](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md) on every node. Select
`Components: main ceph-tentacle` in the wolfSSL repository and `wolfssl-fips-pve-ceph` in
Foundation's package installation. Include `wolfssl-fips-pve-luks` in that transaction if the
node also needs separate local LUKS volumes. Complete SSH preparation, signing-key enrollment
where required, reboot and verification before continuing.

Use the same channel on all nodes. FIPS Ready is not a FIPS-validated module; the licensed
channel's applicability remains subject to the module Security Policy as described in Foundation.
Check the installed Ceph packages on every node:

```sh
dpkg-query -W ceph-common ceph-mon ceph-mgr ceph-osd ceph-volume wolfssl-fips-pve-ceph
```

Require Ceph 20.2.5 wolfSSL builds (`~wolfcryptfips` in the Ceph package versions).
The metapackage supplies the Ceph service environment and compatible cryptsetup/ceph-volume
settings. Ceph daemons, kernel Ceph clients and QEMU's RBD client use wolfCrypt. Ceph
commands run from an administrator's shell, such as `ceph` and `rbd`, use OpenSSL's default
provider.

Before any Ceph daemon exists, the verifier's Ceph-daemon check can fail because no daemon is
running. Corosync is also not yet running. Resolve other applicable failures before continuing;
repeat full verification after cluster creation.

## 3. Create the cluster and encrypted OSDs

First create and verify the new Proxmox cluster using [Cluster, section 2](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md#2-create-and-verify-the-cluster).
All nodes must have completed sections 1–2 above. Do not initialize Ceph before that cluster
has quorum and its corosync verification passes.

On the first node, initialize Ceph with the planned Ceph network:

```sh
pveceph init --network <ceph-network-cidr>
pveceph mon create
```

The first monitor also creates the first manager; do not create a second manager on that node.
Set the following once before creating OSDs. The temporary `secure crc` setting allows the
initial daemons to establish connections while secure mode is configured; section 5 removes
CRC mode before production use.

```sh
for setting in ms_cluster_mode ms_service_mode ms_client_mode ms_mon_cluster_mode ms_mon_service_mode ms_mon_client_mode; do
    ceph config set global "$setting" 'secure crc'
done
ceph config set global ms_bind_msgr2 true
ceph config set global plugin_crypto_accelerator crypto_openssl
ceph config set global rbd_default_map_options ms_mode=secure
```

Create the additional [monitors](https://pve.proxmox.com/pve-docs/chapter-pveceph.html#pve_ceph_monitors)
and [managers](https://pve.proxmox.com/pve-docs/chapter-pveceph.html#pve_ceph_manager)
required by the planned topology.
Check `ceph mon stat` for the intended monitor count and quorum membership.

**Warning:** OSD creation consumes the selected disk. Confirm that `<osd-disk>` is a dedicated,
empty data disk before running this command on each OSD host:

```sh
pveceph osd create <osd-disk> --encrypted 1
ceph-volume lvm list
```

The packaged ceph-volume selects AES-256-CBC with ESSIV-SHA256 and PBKDF2-SHA256 instead of the
stock encryption defaults. It also selects compatible keyslot parameters. Inspect each new
OSD's block device, and its separate DB/WAL devices if configured; obtain their paths from
`ceph-volume lvm list`:

```sh
cryptsetup luksDump <osd-luks-device>
```

Require `aes-cbc-essiv:sha256`, a 256-bit key and PBKDF2 with SHA-256 for the applicable data
and keyslot metadata. No slot may use Argon2 or exceed the licensed module's 10,000,000-iteration
ceiling. Section 5's verifier checks these storage properties as well.

OSD unlock keys are held in the Ceph monitor store and made available during OSD activation;
this is automatic cluster-managed unlocking, not an operator passphrase prompt at each boot.
Protect the monitor store, Ceph credentials and their recovery copies. Encrypted OSDs do not
encrypt the Proxmox root disk, monitor metadata, node-local backup files or every storage on
the node. Follow [Backup](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md) for an independent backup
and tested restore with retained encryption keys.

A cluster created on this Ceph 20.2.5 build has AES-256 cephx keys from creation; no cephx key
migration is required.

## 4. Configure Ceph storage clients

Create pools and register RBD storage using the
[Proxmox Ceph instructions](https://pve.proxmox.com/pve-docs/chapter-pveceph.html).
Create each pool with `--pg_autoscale_mode on`, or set it afterward with
`ceph osd pool set <pool> pg_autoscale_mode on`. With the default `warn` mode, a new cluster
holding little data reports `HEALTH_WARN` for too many placement groups, and section 5's
verifier fails its Ceph health check. All OSDs backing those pools must be encrypted.
Encryption does not follow a guest moved to another, unencrypted storage.

For CephFS, first create the metadata servers required by the planned topology using the
[Proxmox MDS instructions](https://pve.proxmox.com/pve-docs/chapter-pveceph.html#pveceph_fs_mds).
Run `pveceph mds create` on each planned MDS host. At least one MDS must be running in standby
before creating the filesystem.

Require `ceph health` to report `HEALTH_OK` before continuing. Wait for any placement-group
reduction to complete; this can take several minutes. Exceeding the placement-group limit can
prevent creation of the filesystem pools. Check the task output and verify that the filesystem
exists: `pveceph fs create` can return exit status `0` even when its creation task fails.

Create the filesystem without automatic Proxmox storage registration. Replace `<cephfs-name>`
with the filesystem name and select pool settings using the Proxmox instructions:

```sh
pveceph fs create --name <cephfs-name> --add-storage 0
ceph fs ls
```

Require `<cephfs-name>` in the `ceph fs ls` output. If absent, resolve the creation failure
before configuring or registering the storage.

Complete the applicable client configuration below before registering CephFS storage.

The supplied kernel supports encrypted kernel Ceph clients, including `krbd 1`, containers on
RBD, VM TPM-state volumes on RBD, and kernel CephFS mounts. RBD with `krbd 0` uses QEMU's
userspace client through the Foundation wrapper. A TPM-state volume on encrypted RBD is
protected storage; this does not put vTPM cryptographic operations inside wolfCrypt's validated
boundary. Choose the normal Proxmox RBD client setting for the workload.

### 4.1 Kernel CephFS

Before registering a kernel CephFS storage, set every `mon_host` address in the `[global]`
section of `/etc/pve/ceph.conf` to its msgr2 endpoint, including port 3300. Obtain the endpoints
from the monitor map:

```sh
ceph mon dump
nano /etc/pve/ceph.conf
```

For example, use `<monitor-address>:3300`, or `[<ipv6-address>]:3300`. Proxmox shares this file
across the cluster. Register the storage with `ms_mode=secure` in the initial command, because
registration mounts it immediately. Replace `<cephfs-storage>` with the Proxmox storage ID:

```sh
pvesm add cephfs <cephfs-storage> --fs-name <cephfs-name> \
  --content backup,iso,vztmpl,snippets --options ms_mode=secure
```

Include other required mount options in the same comma-separated list. Before writing data to
these mounts, reboot each node in turn, waiting for `HEALTH_OK` before rebooting the next.
Then verify the mounts as described in section 5.
After adding a monitor later, add port 3300 to its new `mon_host` entry;
`pveceph mon create` writes the entry without a port.

### 4.2 FUSE CephFS

For a CephFS storage configured with `fuse 1`, install this mount-unit environment on every
client node before its first mount:

```sh
mkdir -p /etc/systemd/system/mnt-pve-.mount.d
cat > /etc/systemd/system/mnt-pve-.mount.d/10-wolfprov.conf <<'UNIT'
[Mount]
Environment=OPENSSL_CONF=/etc/wolfprovider/openssl.cnf
UNIT
systemctl daemon-reload
```

If the storage is already mounted, reboot the nodes one at a time to activate the setting.
The Ceph metapackage does not configure these Proxmox-created mount units.

## 5. Require secure connections and verify

On each node with kernel Ceph clients, inspect the active mounts and RBD maps:

```sh
findmnt -t ceph -o TARGET,OPTIONS
grep -H . /sys/bus/rbd/devices/*/config_info
```

Every configured kernel CephFS mount must appear with `ms_mode=secure`; every active RBD map
must also show `ms_mode=secure`. No RBD map is expected until a workload maps an RBD volume;
a missing glob is not evidence of a successful client connection.

Once all nodes and clients are configured, make secure mode mandatory and disable msgr1:

```sh
for setting in ms_cluster_mode ms_service_mode ms_client_mode ms_mon_cluster_mode ms_mon_service_mode ms_mon_client_mode; do
    ceph config set global "$setting" secure
done
ceph config set global ms_bind_msgr1 false
```

Restart Ceph on one node at a time, waiting for monitor quorum and healthy placement groups
before the next node:

```sh
systemctl restart ceph.target
ceph -s
ceph mon stat
```

Monitors use addresses from the monitor map; setting `ms_bind_msgr1` to `false` does not
remove their msgr1 listeners. Before removing those endpoints, complete section 4.1's `mon_host`
configuration if kernel CephFS is used. Process one monitor at a time. Run `ceph mon dump` and
identify the monitor's name and msgr2 endpoints. Replace `<mon-id>` with the name following
`mon.` and `<monitor-address>` with its IP address. Enclose IPv6 addresses in brackets, as in
`[v2:[2001:db8::1]:3300]`. If the monitor has multiple msgr2 endpoints, retain all intended
endpoints in the address list:

```sh
ceph mon set-addrs <mon-id> '[v2:<monitor-address>:3300]'
```

Restart the monitor on its own node. Before processing another, require the restarted monitor
and all other intended monitors in quorum, and require `ceph health` to report `HEALTH_OK`:

```sh
systemctl restart ceph-mon@<mon-id>
ceph mon stat
ceph health
```

After the last monitor, check the monitor map and, on each monitor node, the listening ports:

```sh
ceph mon dump
ss -ltnH | awk '{print $4}' | grep -E ':(6789|3300)$'
```

Require every intended monitor to advertise only msgr2 endpoints on port 3300. On each monitor
node, require a listener on port 3300 and no listener on port 6789.

Finally, run on every node:

```sh
wolfssl-fips-pve-verify --ceph-cluster
ceph -s
ceph mon stat
```

Resolve every failure and review warnings, including plaintext `local` or `local-lvm` storage
eligibility. Require all intended OSDs `up` and `in`, all placement groups `active+clean`, all
intended monitors in quorum, and all intended managers and metadata servers listed by `ceph -s`.
`HEALTH_OK` can be reported while a standby is missing. If a daemon is missing and its unit on
that node reports `Start request repeated too quickly`, clear the start limit and start it.
Replace `<id>` with the daemon's name, usually the node name:

```sh
systemctl reset-failed ceph-mgr@<id>
systemctl start ceph-mgr@<id>
```

Use `ceph-mds@<id>` for a metadata server. The verifier checks provider activation, messenger
policy, OSD encryption and relevant kernel clients; it does not prove every guest volume or backup
uses encrypted storage. Check those destinations before creating or importing guests.

## 6. Maintain the cluster

Use [Node maintenance](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md) for APT updates
and access recovery, and [Cluster maintenance](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md#5-maintain-and-restart-the-cluster)
for serial reboots. Installing an update does not activate every running Ceph daemon; complete
the required reboot and re-run section 5 on each node. Wait for clean placement groups before
proceeding to the next node.

For a replacement OSD, follow Proxmox's
[OSD replacement procedure](https://pve.proxmox.com/pve-docs/chapter-pveceph.html#pve_ceph_osd_replace), create the
replacement with `--encrypted 1`, and repeat the header and cluster checks in sections 3 and 5.
For an added node, repeat sections 1–2 before joining it through the Cluster guide. Create its
required Ceph services and encrypted OSDs. If a monitor is added, `pveceph mon create` registers
both msgr2 and msgr1 addresses. For kernel CephFS, first add port 3300 to the new `mon_host`
entry as described in section 4.1. Then configure the monitor's msgr2-only address list, restart
it and complete section 5's checks. Verify the node before assigning guests.

If a monitor is missing from `ceph mon stat`, inspect its service and monitor-map membership
before continuing. If a CephFS mount fails after disabling msgr1, check port 3300 in
`mon_host` and `ms_mode=secure` on that storage before retrying. Do not restore CRC mode as a
permanent workaround.
