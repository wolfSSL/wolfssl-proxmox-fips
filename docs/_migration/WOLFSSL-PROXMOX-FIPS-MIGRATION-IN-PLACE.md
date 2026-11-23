# wolfSSL FIPS for Proxmox VE — In-place conversion

Convert an existing Proxmox VE 9.2 installation by installing the wolfSSL packages on the
running OS, rebooting, and replacing storage encryption and keys created before conversion.
This guide covers a three-node hyper-converged Ceph cluster without a spare host, a standalone
LUKS node, or a standalone ZFS node. Reinstalling and rejoining nodes is outside this procedure.

Run commands as root on the node unless labeled otherwise. Replace angle-bracket placeholders
with the site's values. Stop at a failed command or unmet check before continuing; an expected
authentication refusal is identified explicitly below.

## 1. Requirements and sequence

Use Proxmox VE 9.2 at versions compatible with the selected channel's rebuilt support packages.
If newer Proxmox packages require support versions the channel does not provide, conversion
must wait for matching rebuilds; this guide provides no downgrade to an older Proxmox set.
Only an **ext4/LVM root with separate data disks** is covered; ZFS and encrypted roots are
outside scope. Have console access throughout installation and reboot. Secure Boot may be
disabled or use the signed shim/GRUB path described in [Foundation, section 4](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#4-enroll-the-kernel-signing-key).

Retain current guest backups and protected off-node copies of LUKS headers and keys or ZFS
keys. Keep pre-conversion backups off-node or on encrypted storage according to site policy.
Archives on installer `local` remain plaintext after conversion; changing its content types
does not remove them. Delete those plaintext copies after section 10 passes.
Record guest data checks for comparison after conversion. Rewrap PBS keys that require scrypt
before conversion using
[Migration from Proxmox VE, section 1](WOLFSSL-PROXMOX-FIPS-MIGRATION-PVE.md#1-prepare-the-destination).
Retain the original backup encryption keys. Review
[Foundation's cryptographic scope and repository choice](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#1-requirements-and-repository-choice)
and use its selected channel throughout, with the same channel on every cluster node.

| Backend | Required starting state |
| --- | --- |
| Ceph | Three OSD hosts with quorum and capacity to run every guest on two nodes; stock Tentacle 20.2.5, with the cluster created on 20.2.4 or later. Verify pools, keys and health below. Existing encrypted OSDs will be drained and recreated. Proxmox RBD and kernel CephFS storage use `client.admin`. |
| LUKS | An existing LUKS2 data volume, opened with a key file through `/etc/crypttab`, mounted through `/etc/fstab`, and registered as directory storage with `is_mountpoint yes`. The stock `cryptsetup` CLI must be present. Before replacing cryptsetup in section 4, complete section 3.2: every key file and passphrase must be at least 14 bytes, and every keyslot must use PBKDF2 with at most 10,000,000 iterations. |
| ZFS | An existing data pool with an unencrypted pool root and a separate AES-256-GCM encryption root, a raw key file and an administrator-managed boot key-loading unit. Verify the layout and capacity below. |

On a ZFS node, replace `<pool>` with the data pool and `<old-dataset>` with the old encrypted
dataset. Before any change, check both roots:

```sh
zfs get -H encryption,encryptionroot <pool> <old-dataset>
```

Require `<pool>` to show `encryption off` and `encryptionroot -`, and `<old-dataset>` to show
`encryption aes-256-gcm` and itself as `encryptionroot`. Pool-root encryption is outside scope.
The old tree and the new `<pool>/secure` tree must be disjoint; stop on any other layout.

Budget free pool space for the full configured size of every volume arriving from another
storage, plus the largest volume moved within the pool, plus operational headroom. The new
`zsecure` storage is not sparse: budget full volume sizes, not current usage. Deleting a source
on another storage frees no pool space. Section 7.2 checks available space before every move.

Before any change, inventory snapshots on **every node**; these commands list that node's guests:

```sh
for id in $(qm list | awk 'NR>1 {print $1}'); do echo "== VM $id"; qm listsnapshot $id; done
for id in $(pct list | awk 'NR>1 {print $1}'); do echo "== CT $id"; pct listsnapshot $id; done
```

On every Ceph node, also record the hosted monitor, manager and metadata-server instances:

```sh
for d in /var/lib/ceph/mon/ceph-* /var/lib/ceph/mgr/ceph-* /var/lib/ceph/mds/ceph-*; do [ -d "$d" ] && echo "$d"; done
```

Read the listed directories even if the loop exits 1 because the last MDS directory is absent.
Each directory identifies a hosted role and its instance ID after `ceph-`. Use this inventory
throughout conversion and finalization. The monitor commands below expect three monitors with
IDs matching their hostnames. Managers and metadata servers need not run on every node, but
each active role must have a standby on another node for section 8.3's rolling key replacement.
A role without a standby is outside this procedure. On an RBD-only cluster, omit CephFS steps
and absent MDS entities.

On one Ceph node, verify the starting state:

```sh
ceph versions
ceph -s
ceph osd pool ls detail
ceph osd crush rule dump
ceph pg stat
pveceph auth status
```

Require Tentacle 20.2.5 for every daemon, `HEALTH_OK`, replicated pools with size 3/min_size 2,
a host failure domain in each pool's CRUSH rule, all PGs `active+clean`, and current keys using
`aes256k` with no pending keys. Check the `mgr:` standbys and, with CephFS, at least one MDS
standby in `ceph -s`.

On one Ceph node, record the HA configuration and status before changing any resource:

```sh
ha-manager config
ha-manager status
```

Keep each resource's requested state; an omitted `state` means `started`. No guest may remain
HA-managed during conversion: HA migration omits the storage mapping, and HA recovery or
placement rules can repopulate an evacuated node. If the configuration lists resources, set
**every** one to `ignored`, using its `vm:<vmid>` or `ct:<ctid>` identifier for `<sid>`:

```sh
ha-manager set <sid> --state ignored
```

Repeat the two inventory commands and require every resource's configuration to show
`state ignored` before continuing. If the configuration is empty, there is nothing to change.
Keep these states until section 10's acceptance checks pass, then restore the recorded states.

**Important:** Accept the Ceph redundancy window before starting: each node's OSD rebuild runs
with two copies until backfill completes. A second failure during that window blocks I/O.
Convert one node at a time, restoring `HEALTH_OK`, all PGs `active+clean` and `Quorate: Yes`
before starting another. OSD changes, daemon restarts and node reboots can stall guest I/O.

LUKS and ZFS guests can remain running during package installation and most storage work;
schedule downtime for node reboots, container moves and TPM-state moves. Online LUKS
re-encryption can slow guest I/O. Separate Ceph DB/WAL devices, older Ceph clusters, dedicated
Ceph storage users, external holders of the admin key, and unencrypted ZFS guest datasets need a
separately reviewed procedure. This guide does not cover HA failover or replication-job conversion.

Before section 2 or any data change, complete
[Foundation, section 3](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#3-add-the-repository-and-preferences)
on **every node**, including `ceph-tentacle` for Ceph or both preference packages for ZFS.
Then simulate the installation on each node, replacing `<profile>` with `ceph`, `luks` or `zfs`:

```sh
apt -s install --allow-downgrades wolfssl-fips-pve-<profile>
```

Require a satisfiable transaction and review it against section 4's channel summaries.
An unsatisfiable-dependency report, such as `Depends: … but it is not going to be installed`
with exit status 100, stops conversion before storage changes. Do not run a general upgrade
until that node completes Foundation section 5, including while earlier cluster nodes are
being converted.

After taking the backups and passing the dependency check, remove snapshots referencing
volumes that must move: those on `local-lvm`, `local` or the old ZFS dataset, and local
volumes moved during Ceph guest evacuation. Complete this before section 4 or any volume move; require only
`current` (`You are here!`) for affected guests. Snapshots on volumes that remain in place,
including RBD and the LUKS directory re-encrypted in section 6, can remain.

The required moves refuse affected snapshots. Do not omit `--delete 1` to bypass this:
snapshots would remain on the source, preventing its retirement and invalidating the ZFS
temporary-copy capacity budget.

After these checks, follow the appropriate path:

- **Ceph:** complete section 2 once while all nodes are stock. For each node, complete sections
  3.1, 4 and 5. After every node is converted, complete sections 8–10.
- **LUKS:** complete sections 3.2, 4, 6, 9 and 10.
- **ZFS:** complete sections 4, 7, 9 and 10.

## 2. Prepare the stock Ceph cluster

Run this section on one node while all three nodes still use stock packages. During the mixed
window, log in to the web UI and API on an unconverted node because tickets issued by a
converted node are rejected by unconverted nodes. Mixed stock/wolfSSL membership is a
transitional state only, not a supported steady state.

### 2.1 Select corosync's OpenSSL crypto model

Require `Quorate: Yes` and all three members before editing the shared configuration:

```sh
corosync-cmapctl | grep -E "totem\.(crypto|secauth)|config_version"
cp /etc/pve/corosync.conf /etc/pve/corosync.conf.new
```

Edit `/etc/pve/corosync.conf.new`. In the existing `totem` section, set the following and
increment the existing `config_version` by one, preserving the other cluster settings:

```text
crypto_model: openssl
crypto_cipher: aes256
crypto_hash: sha256
secauth: on
config_version: <current-version + 1>
```

Publish the file, then check the live configuration and quorum:

```sh
mv /etc/pve/corosync.conf.new /etc/pve/corosync.conf
corosync-cmapctl | grep -E "totem\.(crypto|secauth)|config_version"
journalctl -u corosync --since -2min --no-pager | tail -15
pvecm status | grep -E "Quorate|Total votes|Config Version"
```

Require the new configuration version, `openssl`, `aes256` and `sha256`, with `Quorate: Yes`
and three votes. Stock corosync applies this change live. Keep the cluster's existing
membership; the [Cluster guide's creation procedure](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md#2-create-and-verify-the-cluster)
is for new clusters.

### 2.2 Prepare Ceph messaging and clients

Require `HEALTH_OK` before applying the transitional settings:

```sh
ceph -s
ceph osd set noout
for k in ms_cluster_mode ms_service_mode ms_client_mode ms_mon_cluster_mode ms_mon_service_mode ms_mon_client_mode; do
  ceph config set global $k 'secure crc'
done
ceph config set global ms_bind_msgr2 true
ceph config set global plugin_crypto_accelerator crypto_openssl
ceph config set global rbd_default_map_options ms_mode=secure
ceph osd unset noout
ceph config dump | grep -E "ms_|plugin_crypto|rbd_default"
```

Require all six messenger modes to show `secure crc`, msgr2 enabled, `crypto_openssl`, and
`rbd_default_map_options` set to `ms_mode=secure`. Section 8 removes the transitional CRC mode.

```sh
cp /etc/pve/ceph.conf /root/ceph.conf.before-msgr2
ceph mon dump
```

Edit `mon_host` in the `[global]` section of `/etc/pve/ceph.conf` to use each monitor's v2
endpoint on port 3300, as described in
[Ceph, section 4.1](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md#41-kernel-cephfs).
Preserve all intended monitor addresses, then check the result:

```sh
grep -n mon_host /etc/pve/ceph.conf
```

For each kernel CephFS storage, replace `<cephfs-storage>` with its Proxmox storage ID. Inspect
the named storage first:

```sh
pvesh get /storage/<cephfs-storage>
```

If the storage has no `options` line:

```sh
pvesm set <cephfs-storage> --options ms_mode=secure
```

If it already has an options list, copy that list as `<existing-options>` below; `--options`
replaces the whole list. If the list already contains `ms_mode=`, replace that entry with
`ms_mode=secure` in the submitted list instead of appending a second entry:

```sh
pvesm set <cephfs-storage> --options <existing-options>,ms_mode=secure
```

Check that earlier options remain and `ms_mode=secure` is present:

```sh
pvesh get /storage/<cephfs-storage>
ceph -s
```

Require the new option and `HEALTH_OK`. Kernel clients take the secure setting at their next
map or mount during each node's conversion reboot.

## 3. Prepare the node before package installation

### 3.1 Ceph: evacuate guests and drain OSDs

Keep every HA resource `ignored` as required in section 1. Replace `<vmid>` and `<ctid>` with
guest IDs, `<target-node>` with another cluster member, and `<rbd-storage>` with the encrypted
RBD storage ID. During conversion, use the following target checks in place of the Ceph row
in [Cluster, section 3](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md#3-migrate-guests-only-to-available-encrypted-storage).
On the target, run:

```sh
pvecm status | grep -E "Quorate|Total votes"
ceph -s
ceph osd tree
```

Require `Quorate: Yes`, three votes, `HEALTH_OK`, and the target's OSDs `up` and `in`
(reweight `1.00000`). On a **converted** target, also inspect its kernel clients:

```sh
findmnt -t ceph -o TARGET,OPTIONS; grep -H . /sys/bus/rbd/devices/*/config_info 2>/dev/null || echo "no rbd maps on $(hostname)"
```

Require `ms_mode=secure` on every CephFS mount and RBD map; a node without RBD maps reports
that absence. Stock clients retain their earlier modes until their conversion reboot.
OSD keyslot-cipher verification belongs to section 5, secure-only modes and msgr1 removal to
section 8.1, and final cluster verification to section 10. These are not mixed-window target
requirements. Back on the node being converted, inventory guests and select the migration:

```sh
qm list; pct list
```

For VMs and containers whose volumes are already on shared Ceph storage:

```sh
qm migrate <vmid> <target-node> --online
pct migrate <ctid> <target-node> --restart
```

For a VM with local disks, move them to encrypted RBD during migration:

```sh
qm migrate <vmid> <target-node> --online --with-local-disks --targetstorage <rbd-storage>
```

For a container with local volumes, map them to encrypted RBD during restart migration:

```sh
pct migrate <ctid> <target-node> --restart --target-storage <rbd-storage>
```

On `<target-node>`, check the migrated container's configuration:

```sh
pct config <ctid>
```

Require `rootfs` and every managed `mp<N>` volume to reference `<rbd-storage>`. Stop if any
volume remains on local storage; migration success alone does not prove encrypted placement.

Require successful migrations and verify the destination volumes. Leave this node's monitor,
manager and metadata server in place. Once guests are evacuated and Ceph reports `HEALTH_OK`,
identify and check only this node's OSDs:

```sh
IDS=$(ceph osd ls-tree $(hostname)); echo "OSDS on $(hostname): $IDS"
ceph -s | head -20
ceph osd ok-to-stop $IDS
```

Require `ok_to_stop` to be true with no unsafe PGs. Mark these OSDs out **before** stopping
them, then wait for Ceph to report them down:

```sh
for id in $IDS; do ceph osd out $id; done
sleep 10
for id in $IDS; do systemctl stop ceph-osd@$id; done
for id in $IDS; do for i in $(seq 1 30); do ceph osd dump | grep -q "^osd\.$id down" && break; sleep 2; done; done
ceph osd tree down
```

Require every ID in `$IDS` to appear as `down` before checking PGs; the loop times out after
60 seconds per OSD. PG statistics read before that point can appear clean while peering is
still pending. Then wait for peering and activation to settle:

```sh
for i in $(seq 1 60); do ceph pg stat | grep -qE "peering|activating|unknown" || break; sleep 5; done; ceph pg stat
```

Do not treat the loop's timeout as a successful check. Require every PG to be active before
continuing; undersized or degraded PGs are expected during this two-copy window.

**Warning:** The next command destroys this node's old OSDs, wipes their headers and removes
their unlock keys. Confirm the OSD IDs and the accepted two-copy window before continuing.
Replace the two `<osd-disk>` entries with this node's OSD disk paths; use the actual disk list
for the node.

```sh
for id in $IDS; do pveceph osd destroy $id --cleanup 1; done
blkid -t TYPE=crypto_LUKS -o device; echo "(LUKS devices left above)"
lsblk -o NAME,SIZE,TYPE,FSTYPE <osd-disk> <osd-disk>
ceph -s | head -20
```

Require successful destruction, no remaining LUKS device, and empty OSD disks. After
destruction, check PGs again and wait until **every PG is active** before installing packages:

```sh
for i in $(seq 1 60); do ceph pg stat | grep -qE "peering|activating|unknown" || break; sleep 5; done; ceph pg stat
```

The loop stops waiting after five minutes; do not continue while any PG remains inactive.
Confirm that this node has no OSD left before disabling its stale activation units:

```sh
ceph osd ls-tree $(hostname)
```

Require no OSD IDs. Every enabled `ceph-volume@lvm-*` unit on this node then belongs to a
destroyed OSD; disable them before reboot so they do not keep retrying. An empty first listing
is expected if no units remain:

```sh
ls /etc/systemd/system/multi-user.target.wants/ | grep '^ceph-volume@lvm-'
for u in $(ls /etc/systemd/system/multi-user.target.wants/ | grep '^ceph-volume@lvm-'); do systemctl disable "$u"; done
ls /etc/systemd/system/multi-user.target.wants/ | grep '^ceph-volume@lvm-' || echo "no ceph-volume activation units enabled"
```

Require no enabled activation units, then continue with section 4. Section 5 creates new OSDs
and enables their units before another node is converted.

### 3.2 LUKS: normalize keyslots and credentials with stock cryptsetup

Use stock cryptsetup before section 4 replaces it. Keep the volume open and mounted; guests
can remain running. Replace `<data-disk>` with the existing data device's persistent
path and `<mapping>` with its mapper name. Read its boot entry, then use the third field as
`<old-keyfile>` and check the file's length:

```sh
awk -v m='<mapping>' '$1 == m' /etc/crypttab
stat -c '%s bytes' <old-keyfile>
```

Require at least 14 bytes for every key file and passphrase. LUKS does not record passphrase
lengths; check each recovery credential before continuing. For non-ASCII passphrases, count
bytes, not characters. A short credential can become unusable after package replacement.

If the boot key file is shorter than 14 bytes, choose an unused `<interim-keyfile>` path in
the protected key directory and replace it now using stock cryptsetup:

```sh
(umask 077; dd if=/dev/urandom of=<interim-keyfile> bs=64 count=1 status=none)
cryptsetup luksAddKey --pbkdf pbkdf2 --hash sha256 --pbkdf-force-iterations 1000000 --key-file <old-keyfile> <data-disk> <interim-keyfile>
cryptsetup open --test-passphrase -v --key-file <interim-keyfile> <data-disk>
```

Require `Key slot <n> unlocked.` and `Command successful.` Change the third field of the
`<mapping>` entry in `/etc/crypttab` to `<interim-keyfile>`, preserving its other fields, and
read it back:

```sh
awk -v m='<mapping>' '$1 == m' /etc/crypttab
```

Only after the new key works and the boot entry names it, remove the short key's slot and
test its refusal:

```sh
cryptsetup luksRemoveKey <data-disk> <old-keyfile>
cryptsetup open --test-passphrase --key-file <old-keyfile> <data-disk>
```

Require `No key available with this passphrase.` and exit status 2 before deleting the file:

```sh
shred -u <old-keyfile>
```

From here onward, use `<interim-keyfile>` wherever this guide says `<old-keyfile>`. Section 6
replaces this stock-generated interim key after conversion.

For each passphrase shorter than 14 bytes, run the following with stock cryptsetup. Enter the
old passphrase, then a new passphrase of at least 14 bytes at the interactive prompts:

```sh
cryptsetup luksChangeKey --pbkdf pbkdf2 --hash sha256 --pbkdf-force-iterations 1000000 \
  --keyslot-cipher aes-cbc-essiv:sha256 --keyslot-key-size 256 <data-disk>
```

Require success before continuing. Inventory every keyslot, including its iteration count:

```sh
for d in $(blkid -t TYPE=crypto_LUKS -o device); do echo "== $d"; cryptsetup luksDump "$d" | sed -n '/^Keyslots:/,/^Tokens:/p' | grep -E '^ +[0-9]+: luks2|PBKDF:|Iterations:'; done
```

Convert every slot that does not use PBKDF2 at or below 10,000,000 iterations, including
Argon2 slots and PBKDF2 slots above that ceiling. Replace `<slot>` with each affected slot
number. Square brackets mark an optional argument: for a key-file slot, include
`--key-file <old-keyfile>` without brackets; for a passphrase slot, omit it and enter the
passphrase at the prompt.

```text
cryptsetup luksConvertKey --pbkdf pbkdf2 --hash sha256 --pbkdf-force-iterations 1000000 --key-slot <slot> [--key-file <old-keyfile>] <data-disk>
```

The explicit 1,000,000 iterations avoids automatic calibration above the module's
10,000,000-iteration ceiling. Inspect the result:

```sh
cryptsetup luksDump <data-disk> | grep -E '^ +[0-9]+: luks2|PBKDF:|Iterations:|Cipher:|cipher:'
```

Require PBKDF2 at or below 10,000,000 iterations for every slot, with 1,000,000 iterations for
converted or replaced slots, and all credentials at least 14 bytes. The data and unchanged
keyslot ciphers can still be XTS; section 6 replaces them after the wolfSSL reboot. The final
`Iterations:` line describes the volume-key digest and is not a keyslot iteration count.

## 4. Install and activate the packages on this node

Review the simulated and actual profile transaction. Counts depend on the starting packages;
the compatible package set gives these summaries, with no upgrades:

| Profile | Licensed v5: installing / downgrading / removing | FIPS Ready: installing / downgrading / removing |
| --- | --- | --- |
| Ceph | 25 / 40 / 0 | 26 / 41 / 1 |
| LUKS | 13 / 19 / 0 | 16 / 20 / 1 |
| ZFS | 15 / 16 / 0 | 18 / 17 / 1 |

Ready replaces `libgcrypt20` with `libgcrypt20-1.11.0-wolfssl`; licensed v5 removes no package.
Accept no other removal. Ready also downgrades `libnss3`; licensed v5 leaves the Debian package
unchanged. Both channels downgrade three backup-client packages, and the SSH install reports
the packages already current after the profile transaction. On Ceph nodes, installation
restarts the manager and can move the active role; monitors and MDS processes switch at reboot.

Complete [Foundation, section 2](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#2-prepare-ssh-access)
to authorize and test an ECDSA or RSA administrative key. Then follow these Foundation sections
in order, using the channel selected there:

1. [Section 3: repository and preferences](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#3-add-the-repository-and-preferences).
   These were configured for section 1's dependency check; repeating this section should make
   no change. Keep the same channel, components and preferences.
2. [Section 4: signing-key enrollment request](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#4-enroll-the-kernel-signing-key).
3. [Section 5: package installation and SSH refresh](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#5-install-the-packages).
   Select only the applicable `ceph`, `luks` or `zfs` profile. Install the backup clients and
   SSH packages too. Refresh host-key records on workstations and on the node itself as directed.

For Ceph, require the monitor stop check to succeed before the conversion reboot:

```sh
ceph mon ok-to-stop $(hostname)
```

Then complete [Foundation, section 6: reboot and verification](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#6-reboot-and-verify).
Attend the console and confirm the MOK promptly. `mokutil --timeout -1` is not a safeguard
against a missed confirmation; use that section's stock-kernel recovery if enrollment is missed.

Package installation does not replace the environment of already-running guests. The reboot,
a full stop/start, or migration to a rebooted converted node activates the packaged wrappers.

Require Foundation's kernel, provider, SSH and management-login checks to pass. Existing Ceph
monitors, managers and metadata servers must run on the new packages without recreation;
the corosync checks must pass. Before storage work, the following findings can remain:

| Backend | Findings resolved by the remaining procedure | Expected result (both channels) |
| --- | --- | --- |
| Ceph | `WARN 21`: no open dm-crypt mapping until OSD recreation; `WARN 25` ×2: installer storage. Cluster verification also rejects transitional messenger settings until section 8.1. | 0 FAIL; `WARN 21` ×1, `WARN 25` ×2, plus `WARN 13` lines |
| LUKS | `FAIL 23` ×4: XTS data/keyslots, unlock unit, mount configuration and backup staging; `FAIL 25` ×2: installer storage. | 6 FAIL; only `WARN 13` warnings |
| ZFS | `FAIL 22` ×3: pool-root mountpoint, old dataset overlay and unlock unit; `FAIL 25` ×2: installer storage. | 5 FAIL; only `WARN 13` warnings |

The number of `WARN 13` lines depends on the upstream Proxmox package versions available when
verification runs. `WARN 13` reports updates held
back until compatible support-package rebuilds are published and does not block acceptance.
As [Node Configuration, section 1](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md#1-update-the-installed-packages)
requires, review held security updates, contact wolfSSL when a required fix awaits a rebuild,
and keep the preferences in place. Resolve every other warning or confirm that its explanation
matches the node's state. Apply this policy whenever verification is repeated.

Resolve failures outside the table before continuing. An encryption-property check on an
existing ZFS dataset cannot establish when its key was generated.

## 5. Ceph: recreate this node's encrypted OSDs

After this node passes section 4, recreate its OSDs using the **encrypted OSD creation and
header inspection** steps in [Ceph, section 3](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md#3-create-the-cluster-and-encrypted-osds),
starting at the OSD-creation warning. Use only the emptied disks from section 3.1. Keep the
existing cluster and daemon roles; skip that section's cluster initialization and daemon
creation steps.

Require AES-256-CBC with ESSIV-SHA256 and PBKDF2-SHA256 in the new OSD headers, including
keyslot metadata, within the documented iteration ceiling. Check placement and health:

```sh
ceph osd tree
ceph pg stat
ceph -s
ceph health detail
pvecm status
```

Wait for every PG to be `active+clean`, all intended OSDs up/in, `HEALTH_OK`, and `Quorate: Yes`.
Only then repeat sections 3.1, 4 and 5 on the next node. After all three nodes are converted,
continue with section 8.

## 6. LUKS: re-encrypt the existing data volume

### 6.1 Replace the volume key and cipher

Require the converted kernel and LUKS integration to be active using
[LUKS, section 2](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-LUKS.md#2-verify-the-luks-integration).
Keep the existing volume open and mounted. Identify the slot unlocked by the existing key file:

```sh
cryptsetup open --test-passphrase -v --key-file <old-keyfile> <data-disk>
```

Use the reported slot number for `<slot>` below.

**Warning:** Re-encryption rewrites the existing volume and deletes the other keyslots,
including the recovery-passphrase slot. Require the current backup, header and key copies
before starting. Schedule a quiet period and retain the working key file until the replacement
has been tested. **Never restore the pre-conversion header once re-encryption has started:**
it lacks the new volume key and would make rewritten regions unreadable. Use the recovery
procedure below if re-encryption is interrupted.

Allow for channel-dependent throughput and guest write stalls: approximately 70–85 MiB/s
with 3.3-second stalls on licensed v5, versus 40 MiB/s with 6-second stalls on FIPS Ready.
These figures are planning references, not throughput guarantees.

```sh
cryptsetup reencrypt --cipher aes-cbc-essiv:sha256 --key-size 256 --pbkdf pbkdf2 --hash sha256 \
  --pbkdf-force-iterations 1000000 --key-file <old-keyfile> --key-slot <slot> <data-disk>
```

Require `Finished, time …`, successful completion and the original mount to remain available
before changing keyslots. If uninterrupted, continue at **Restore the recovery passphrase**.

#### Resume interrupted re-encryption

If interrupted while the volume remains open, resume with the same key file:

```sh
cryptsetup reencrypt --resume-only --key-file <old-keyfile> <data-disk>
```

After a crash or power loss, boot-time opening performs recovery but may leave re-encryption
unfinished. If the volume opens, check:

```sh
cryptsetup status <mapping>
```

If it reports `reencryption: in-progress`, run the resume command above even though the
volume is mounted and guests have started. If the volume does not open, preserve its current
header before explicit repair. Replace `<mountpoint>` with the existing mountpoint; its
original `fstab` entry is still present:

```sh
(umask 077; cryptsetup luksHeaderBackup <data-disk> --header-backup-file /root/<mapping>-header-interrupted.img)
cryptsetup repair --key-file <old-keyfile> <data-disk>
cryptsetup open --key-file <old-keyfile> <data-disk> <mapping>
mount <mountpoint>
```

Then run the resume command above. Require successful completion before continuing.
`--resume-only` refuses to start a new operation; if it reports that no re-encryption is
recorded, there is nothing to resume. Other errors must be resolved. If the volume cannot be
recovered and resumed, restore guests from the backups taken before conversion.

#### Restore the recovery passphrase

After re-encryption completes, the remaining keyslot still needs its cipher changed.
Convert it, then re-add a recovery passphrase of at least 14 bytes at the interactive prompts:

```sh
cryptsetup luksConvertKey --keyslot-cipher aes-cbc-essiv:sha256 --keyslot-key-size 256 --pbkdf pbkdf2 --hash sha256 --pbkdf-force-iterations 1000000 --key-file <old-keyfile> <data-disk>
cryptsetup luksAddKey --pbkdf pbkdf2 --hash sha256 --pbkdf-force-iterations 1000000 --keyslot-cipher aes-cbc-essiv:sha256 --keyslot-key-size 256 --key-file <old-keyfile> <data-disk>
```

### 6.2 Replace the boot key file

Choose `<new-keyfile>` as a new filename in a protected key directory, distinct from the old
file. Generate, add and test it on the converted node; retain the old key until section 6.3
completes the boot handoff:

```sh
(umask 077; dd if=/dev/urandom of=<new-keyfile> bs=64 count=1 status=none)
cryptsetup luksAddKey --pbkdf pbkdf2 --hash sha256 --pbkdf-force-iterations 1000000 --keyslot-cipher aes-cbc-essiv:sha256 \
  --keyslot-key-size 256 --key-file <old-keyfile> <data-disk> <new-keyfile>
cryptsetup open --test-passphrase --key-file <new-keyfile> <data-disk>
```

Require the new-key test to succeed before changing boot configuration.

### 6.3 Hand boot unlock to the package service

Use the same `<mapping>` as in section 3.2. Replace `<mountpoint>` with its existing mountpoint
and `<luks-storage>` with the directory storage ID. Complete the file edits and unit enablement
back to back: a reboot between them leaves the volume closed and its guests unable to start.
Obtain the volume UUID:

```sh
cryptsetup luksUUID <data-disk>
```

Use that value for `<luks-uuid>`. Edit `/etc/crypttab` directly, replacing the existing entry
for `<mapping>` with exactly:

```text
<mapping> UUID=<luks-uuid> <new-keyfile> luks,noauto
```

Edit `/etc/fstab` directly and delete only the entry whose mountpoint is `<mountpoint>`,
whether its source uses a mapper path, `UUID=`, `LABEL=` or another device path. Preserve
unrelated entries. Read back the exact mapping and mountpoint:

```sh
awk -v m='<mapping>' '$1 == m' /etc/crypttab
findmnt --fstab --mountpoint <mountpoint>; echo "findmnt rc=$?"
```

Require exactly one `crypttab` line matching the entry above, no `findmnt` output and
`findmnt rc=1`. Stop on a missing or duplicate unlock entry or a surviving mount entry.
Immediately configure and enable the packaged unit:

```sh
mkdir -p /etc/wolfssl-fips-pve-luks
printf '%s\n' 'MOUNTPOINT=<mountpoint>' > /etc/wolfssl-fips-pve-luks/<mapping>.conf
systemctl daemon-reload
systemctl enable wolfssl-fips-luks@<mapping>.service
```

**Important:** Enable the unit only. Do not start it or use `--now` while the volume is already
mounted; starting it can mount a second layer. The unit takes over at the next boot.

Require the correct `MOUNTPOINT` and enablement links for both `multi-user.target` and
`pve-guests.service.requires`. Stopping the unlock unit after normal startup can stop all guests;
see [LUKS unlock recovery](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-LUKS.md#6-resolve-unlock-failures).

Only after the handoff succeeds, re-test the new key, remove the old keyslot and test refusal:

```sh
cryptsetup open --test-passphrase --key-file <new-keyfile> <data-disk> && cryptsetup luksRemoveKey <data-disk> <old-keyfile>
cryptsetup open --test-passphrase --key-file <old-keyfile> <data-disk>
```

Require `No key available with this passphrase.` and exit status 2 for the old key before
deleting its file and backing up the final header:

```sh
shred -u <old-keyfile>
(umask 077; cryptsetup luksHeaderBackup <data-disk> --header-backup-file /root/<mapping>-header.img)
```

Copy the new header and key to protected off-node recovery storage. The boot key remains on
the unencrypted root filesystem; possession of both disks also provides the key.

### 6.4 Move remaining guest volumes and configure backup staging

Use the `qm config` and `pct config` inventory commands in
[Migration Acceptance, section 1](WOLFSSL-PROXMOX-FIPS-MIGRATION-ACCEPTANCE.md#1-check-the-destination);
its installer-storage and backup-staging verifier findings are resolved below.
Confirm the LUKS mapping and mount before moving data. Include detached disks and all
additional volumes; the commands below cover ordinary VM disks and a container's `rootfs`.
Replace `<vmid>` and `<ctid>` with the VM and container IDs, and `<disk>` with the VM disk
configuration key, such as its boot or data disk key.

**Warning:** `--delete 1` removes the source volume after the copy. Confirm the encrypted
destination and current backups before each move.

```sh
qm disk move <vmid> <disk> <luks-storage> --format qcow2 --delete 1
```

Move TPM state as a raw file with the VM stopped:

```sh
qm shutdown <vmid> --timeout 120
qm disk move <vmid> tpmstate0 <luks-storage> --format raw --delete 1
qm start <vmid>
```

For container root volumes:

```sh
pct shutdown <ctid> --timeout 60
pct move-volume <ctid> rootfs <luks-storage> --delete 1
pct start <ctid>
```

Check the final volume inventory and guest data, then inspect `local-lvm` if present:

```sh
pvesm list local-lvm
```

Only after it is empty, disable it:

```sh
pvesm set local-lvm --disable 1
```

Read `local`'s content list, then use `<local-content-without-backup>` for that list with
`backup`, `images` and `rootdir` removed, retaining other types in use, including `snippets`
and `import`:

```sh
pvesh get /storage/local
pvesm set local --content <local-content-without-backup>
pvesh get /storage/local
```

Require the retained types and no guest-disk or backup content.

Configure encrypted container-backup staging using [LUKS, section 4](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-LUKS.md#4-register-encrypted-proxmox-storage),
starting at the staging instructions and substituting `<mountpoint>` for its example mount.
Allow backups on the existing encrypted directory storage:

```sh
pvesm set <luks-storage> --content images,rootdir,backup
```

Continue with sections 9–10.

## 7. ZFS: move data to a new encryption root

### 7.1 Create encrypted storage on the existing pool

Complete [ZFS activation checks](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-ZFS.md#2-verify-wolfzfs-activation)
after the conversion reboot. Use the pool and old encryption root checked in section 1.
Create `<pool>/secure` with a new raw key at
`/etc/zfs/keys/<pool>-secure.key` and mountpoint `/<pool>/secure`; these paths must be unused.

Follow [ZFS, section 3](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-ZFS.md#3-create-the-encrypted-dataset-and-boot-unlock)
starting with key-directory creation, substituting the existing pool name for that guide's
example. **Skip `zpool create`: the pool already exists.** Create the new encrypted dataset
and enable its packaged unlock service. Defer changing the pool-root mountpoint until section
7.3. Check the old and new roots:

```sh
zfs get -H encryption,keyformat,encryptionroot,keystatus,keyguid,mounted,overlay <pool>/secure <old-dataset>
```

Require the new root to use `aes-256-gcm`, a raw key, `keystatus available`, `overlay off`,
and a key GUID different from the old root's. Retain a protected off-node copy of the new key.

Register `zsecure` and `zbackup` using [ZFS, section 4](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-ZFS.md#4-register-encrypted-proxmox-storage),
through the two storage-registration commands. These are example IDs for the new guest and
backup storage; substitute consistently if already used. Verify the encrypted mount before
registration. Defer disabling installer storage until its volumes have moved below.

### 7.2 Move every volume individually

Inventory every volume with the `qm config` and `pct config` commands in
[Migration Acceptance, section 1](WOLFSSL-PROXMOX-FIPS-MIGRATION-ACCEPTANCE.md#1-check-the-destination).
Defer its verifier findings for the pool root, old dataset and installer storage to section 7.3.
Replace `<vmid>` and `<ctid>` with guest IDs and `<disk>` with each ordinary disk or EFI-state
configuration key. Include volumes on both the old dataset and unencrypted `local-lvm`.

Move one volume at a time. Before **each** move, check the destination's available capacity
and the source volume's configured `size=`:

```sh
zfs list -o name,avail,used <pool>/secure
qm config <vmid>
```

For a container, use `pct config <ctid>` for the configuration read. Require `avail` to exceed
the volume's full configured size plus headroom, using section 1's budget for the whole move
sequence. This applies to ordinary disks, EFI and TPM state, and container volumes.

**Warning:** These moves remove each source volume after copying it. Confirm the target
encryption root and current backups before proceeding. Move TPM state with the VM stopped;
containers also require a shutdown for the volume move.

```sh
qm disk move <vmid> <disk> zsecure --delete 1
```

For TPM state:

```sh
qm shutdown <vmid> --timeout 120
qm disk move <vmid> tpmstate0 zsecure --delete 1
qm start <vmid>
```

For container root volumes:

```sh
pct shutdown <ctid> --timeout 60
pct move-volume <ctid> rootfs zsecure --delete 1
pct start <ctid>
```

Require successful moves, verify guest data, and use the
[ZFS volume checks](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-ZFS.md#5-verify-data-and-unlock-after-reboot)
to confirm every destination volume inherits `<pool>/secure`. Review additional container
mounts and detached disks before retiring the source.

### 7.3 Retire the old dataset and key

Replace `<old-zfs-storage>` with its storage ID, `<old-loadkey-unit>` with the administrator's
old boot key-loading unit, and `<old-zfs-key>` with the old key-file path. The old key must
serve only the dataset being retired. A unit running `zfs load-key -a` also loads the new
root; disable it only after section 7.1's packaged unlock service is enabled. Otherwise,
require the old unit to serve only the retired dataset. Inspect remaining volumes:

```sh
pvesm list <old-zfs-storage>
```

Require an empty storage listing, then preview the recursive destruction before removing
the storage definition:

```sh
zfs destroy -nvr <old-dataset>
```

Review every listed item. Only `<old-dataset>` may be removed; no entry may name `<pool>/secure`
or any descendant. Any child dataset or snapshot is unexpected after the moves and stops
retirement until accounted for. Require a successful dry run; it deletes nothing.

**Warning:** The following destroys the old dataset and its descendants. Proceed only after
the dry run confirms no required data would be removed, then retire its storage and key.

```sh
pvesm remove <old-zfs-storage>
zfs destroy -r <old-dataset>
systemctl disable <old-loadkey-unit>
shred -u <old-zfs-key>
zfs set -u mountpoint=none <pool>
zfs get -H mountpoint,mounted <pool> <pool>/secure; mountpoint /<pool>/secure
```

Keep `-u`: changing the mountpoint without it attempts to unmount the busy pool root.
Require `mountpoint=none` on the pool and the new encrypted mount to remain available; the
pool-root change takes effect at the next boot.

If `local-lvm` exists, inspect it first:

```sh
pvesm list local-lvm
```

Only after it contains no volumes, disable it:

```sh
pvesm set local-lvm --disable 1
```

Apply [section 6.4's `local` content-list edit and read-back](#64-move-remaining-guest-volumes-and-configure-backup-staging),
retaining other types in use.

Complete [Backup destination and staging configuration](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md#2-protect-the-destination-and-temporary-files),
then continue with sections 9–10.

## 8. Finalize the converted Ceph cluster

Begin only after every node has rebooted onto the wolfSSL packages, its OSDs have been
recreated, and the cluster has quorum, `HEALTH_OK` and all PGs `active+clean`.
After the last node is converted, the rolling Ceph restart in section 8.1 and the client
refresh in section 8.3 must re-establish every Ceph connection so no stock-era session key
survives. Complete the restart before re-keying, and refresh every client before committing
the new admin key.

### 8.1 Require secure-only connections

Complete [Ceph, section 5](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md#5-require-secure-connections-and-verify):
check every kernel client for `ms_mode=secure`, set all six messenger modes to `secure`, disable
msgr1, restart Ceph one node at a time, and remove monitor v1 endpoints. Require all intended
monitors in quorum and clean PGs after each restart. Guest I/O stalls can last up to about 30
seconds, mainly on the first node restarted; timing depends on the workload. Every
monitor must advertise and listen on port 3300, with no listener on 6789.

### 8.2 Replace the corosync key

Generate and stage the new key once on a converted node:

```sh
corosync-keygen -k /root/corosync-authkey.new
cp /root/corosync-authkey.new /etc/pve/priv/corosync-authkey.new && rm /root/corosync-authkey.new
```

Install the staged key on **every node before reloading**:

```sh
install -m 0400 /etc/pve/priv/corosync-authkey.new /etc/corosync/authkey
```

After all three installations succeed, reload once and check quorum:

```sh
corosync-cfgtool -R
journalctl -u corosync --since -1min --no-pager | grep -E 'crypto reconfigured' | tail -3
pvecm status | grep -E "Quorate|Total votes"
```

Require crypto reconfiguration, `Quorate: Yes` and three votes before removing the staged copy:

```sh
rm /etc/pve/priv/corosync-authkey.new
```

### 8.3 Replace the cephx keys

**Required:** Replace the stock-generated cephx keys after conversion even when they already
use `aes256k`. The Proxmox service-key migration helper does not replace every existing
`aes256k` key. The recreated OSDs' OSD, lockbox and dm-crypt keys are already new; retain them.

```sh
pveceph auth status
ceph auth ls | grep -E "^(osd|mon|mgr|mds|client)\." | sort
```

Require `aes256k` current keys and no pending keys. Use this entity listing and the per-node
daemon inventory from section 1 for the rotations below. Apply daemon keys only to instances
hosted on that node, using their actual IDs.

Create the protected directory for old-key probes on **every node**, before copying any keys:

```sh
install -d -m 0700 /root/cephx-compare
```

Choose an unused `<staging>` directory under the root-only, cluster-shared `/etc/pve/priv`.
Create it once on the node that will run the rotations and keep the cluster-wide old-key copies:

```sh
mkdir -p <staging>
```

Keyrings and JSON contain secrets; keep them out of terminal output and shared logs. Keep
the old copies until the refusal checks succeed, then remove both temporary directories below.

#### Monitor key

Once, on the rotation node, save its old monitor keyring before rotating:

```sh
cp /var/lib/ceph/mon/ceph-$(hostname)/keyring /root/cephx-compare/mon.old.keyring
ceph auth rotate mon. --key_type aes256k -f json > /dev/null
ceph auth get mon. > <staging>/mon.keyring
ceph-authtool /etc/pve/priv/ceph.mon.keyring --import-keyring <staging>/mon.keyring
```

Update the local keyring on **every monitor node before any restart**:

```sh
ceph-authtool /var/lib/ceph/mon/ceph-$(hostname)/keyring --import-keyring <staging>/mon.keyring
chown ceph:ceph /var/lib/ceph/mon/ceph-$(hostname)/keyring
```

Then process one monitor node at a time. Require the stop check to preserve quorum before
running the restart:

```sh
ceph mon ok-to-stop $(hostname)
```

```sh
systemctl reset-failed ceph-mon@$(hostname); systemctl restart ceph-mon@$(hostname)
ceph mon stat; ceph health
```

Require all three monitors in quorum and `HEALTH_OK` after each restart. After the last
restart, run both probes on the rotation node:

```sh
timeout 30 ceph -n mon. -k /root/cephx-compare/mon.old.keyring --connect-timeout 10 mon stat; echo "exit status $?"
timeout 30 ceph -n mon. -k /var/lib/ceph/mon/ceph-$(hostname)/keyring --connect-timeout 10 mon stat; echo "exit status $?"
```

For this and the following old/new-key checks, require the old key to fail with status 13 and
`RADOS permission denied (error connecting to the cluster)`, and the new key to return the
monitor map with status 0. A missing-file error or timeout is not proof of key refusal.

#### Manager, metadata-server, bootstrap and crash keys

On the rotation node, replace `<entity>` with each existing `client.bootstrap-osd`,
`client.bootstrap-mds`, `client.bootstrap-mgr`, `client.bootstrap-rbd`,
`client.bootstrap-rbd-mirror`, `client.bootstrap-rgw` and `client.crash`. Run once per listed
entity; omit absent entities. Manager and MDS keys are rotated on their own nodes below.

```sh
ceph auth get <entity> > /root/cephx-compare/<entity>.old.keyring
ceph auth rotate <entity> --key_type aes256k -f json > /dev/null
ceph auth get <entity> > <staging>/<entity>.keyring
```

If `client.crash` is present, update and probe its shared keyring once:

```sh
ceph-authtool /etc/pve/ceph/ceph.client.crash.keyring --import-keyring <staging>/client.crash.keyring
timeout 30 ceph -n client.crash -k /etc/pve/ceph/ceph.client.crash.keyring --connect-timeout 10 mon stat; echo "DEPLOYED client.crash exit status $?"
```

Require the monitor map and `DEPLOYED client.crash exit status 0`.
On each node in turn, check health and standby availability before rotating any hosted key:

```sh
ceph -s
```

Require `HEALTH_OK` and a standby on another node for each role active here: a manager in
`mgr:`'s standby list and, with CephFS, at least `1 standby` in `mds:`. Stop if either needed
standby is missing. Save, stop, rotate, install and restart each hosted instance in this order;
the loop derives its ID from its directory and skips absent roles:

```sh
for d in /var/lib/ceph/mgr/ceph-* /var/lib/ceph/mds/ceph-*; do
  [ -d "$d" ] || continue
  t=$(basename "$(dirname "$d")"); i=${d##*/ceph-}
  cp "$d/keyring" /root/cephx-compare/$t.$i.old.keyring
  systemctl stop ceph-$t@$i
  ceph auth rotate $t.$i --key_type aes256k -f json > /dev/null
  ceph auth get $t.$i > <staging>/$t.$i.keyring
  ceph-authtool "$d/keyring" --import-keyring <staging>/$t.$i.keyring; chown ceph:ceph "$d/keyring"
  systemctl reset-failed ceph-$t@$i; systemctl restart ceph-$t@$i
done
```

On this node, list every deployed bootstrap keyring, then update and probe each copy:

```sh
ls -l /var/lib/ceph/bootstrap-*/ceph.keyring
```

If no file exists, `ls` reports `No such file or directory` with status 2; there is no deployed
copy on this node. Otherwise, account for every listed file:

```sh
for f in /var/lib/ceph/bootstrap-*/ceph.keyring; do
  [ -f "$f" ] || continue
  e=client.$(basename "$(dirname "$f")")
  ceph-authtool "$f" --import-keyring <staging>/$e.keyring
  timeout 30 ceph -n $e -k "$f" --connect-timeout 10 mon stat; echo "DEPLOYED $e exit status $?"
done
```

Require every deployed-key probe to return the monitor map with status 0. Proxmox nodes
normally deploy only `client.bootstrap-osd`; `client.bootstrap-mds`, `client.bootstrap-mgr`,
`client.bootstrap-rbd`, `client.bootstrap-rbd-mirror` and `client.bootstrap-rgw` have no deployed
consumer there, but any extra installed copy must pass the same import and probe.
Then verify this node's manager and MDS instances:

```sh
sleep 15
for d in /var/lib/ceph/mgr/ceph-* /var/lib/ceph/mds/ceph-*; do
  [ -d "$d" ] || continue
  t=$(basename "$(dirname "$d")"); i=${d##*/ceph-}
  systemctl is-active ceph-$t@$i
  timeout 30 ceph -n $t.$i -k /root/cephx-compare/$t.$i.old.keyring --connect-timeout 10 mon stat; echo "OLD $t.$i exit status $?"
  timeout 30 ceph -n $t.$i -k "$d/keyring" --connect-timeout 10 mon stat; echo "NEW $t.$i exit status $?"
done
ceph -s
```

Require each hosted service active, all inventoried daemons present, quorum, clean PGs,
`HEALTH_OK` and successful old/new-key checks before another node. Check active and standby
roles after failover. A node without manager or MDS directories has no daemon-loop output.
After every node is updated, probe the bootstrap and crash keys on the rotation node; these
staged-copy probes also cover entities with no deployed consumer. Omit absent entities:

```sh
for e in client.bootstrap-osd client.bootstrap-mds client.bootstrap-mgr client.bootstrap-rbd client.bootstrap-rbd-mirror client.bootstrap-rgw client.crash; do
  timeout 30 ceph -n $e -k /root/cephx-compare/$e.old.keyring --connect-timeout 10 mon stat; echo "OLD $e exit status $?"
  timeout 30 ceph -n $e -k <staging>/$e.keyring --connect-timeout 10 mon stat; echo "NEW $e exit status $?"
done
```

Require the same old-key refusal and new-key success. If a new-key probe instead reports
`Error EACCES: access denied` without `error connecting to the cluster`, authentication
succeeded but the command was denied; this still proves the new key authenticates.

#### Proxmox storage client key

Keep `client.admin`'s old key valid until every client has refreshed. On the rotation node,
inspect its pending-key promotion policy:

```sh
ceph config help mon_auth_client_pending_key_auto_promote
ceph config get mon mon_auth_client_pending_key_auto_promote
```

Require the original value to be the default `true`. Disable automatic promotion, retain
the old key and create the pending key:

```sh
ceph config set mon mon_auth_client_pending_key_auto_promote false
cp /etc/pve/priv/ceph.client.admin.keyring /root/cephx-compare/admin.old.keyring
ceph auth get-or-create-pending client.admin -f json > <staging>/admin-pending.json
```

From `pending_key` in the protected JSON file, update all these copies without changing
their format:

- `/etc/pve/priv/ceph.client.admin.keyring`: replace its `key` value.
- `/etc/pve/priv/ceph/<rbd-storage>.keyring`: replace its `key` value for each RBD storage.
- `/etc/pve/priv/ceph/<cephfs-storage>.secret`: replace the bare key for each CephFS storage.

Test the updated default keyring and then the old keyring, and confirm the key remains pending:

```sh
timeout 30 ceph --connect-timeout 10 mon stat; echo "new (pending) key exit status $?"
timeout 30 ceph -k /root/cephx-compare/admin.old.keyring --connect-timeout 10 mon stat; echo "old key exit status $?"
ceph auth get client.admin | grep -c "pending_key\|pending key"
```

Require **both keys to succeed with status 0**, including after the pending key has been used,
and one pending entry.

Refresh every running Ceph-backed VM and container using the migrations from section 3.1,
now between converted nodes. Refresh CephFS on every node after arranging for the mount to
be unused:

```sh
umount /mnt/pve/<cephfs-storage>; pvesm status --storage <cephfs-storage>
```

The first status can show `inactive` while Proxmox remounts the storage. Repeat the status
command until it reports `active`, then inspect the new mount:

```sh
pvesm status --storage <cephfs-storage>
findmnt -t ceph -o TARGET,OPTIONS
```

Require successful unmount, active storage and `ms_mode=secure` on the new mount. Commit
only after every client has refreshed:

```sh
ceph auth commit-pending client.admin
```

**Important:** A successful commit prints nothing. `no pending key` is not successful
completion of this sequence even if the exit status is 0; automatic promotion may have
invalidated the old key before clients refreshed.

After commit, test both keys:

```sh
timeout 30 ceph -k /root/cephx-compare/admin.old.keyring --connect-timeout 10 mon stat; echo "OLD admin exit status $?"
timeout 30 ceph --connect-timeout 10 mon stat; echo "NEW admin exit status $?"
```

Require old-key refusal with status 13 and the connection-authentication error described
above, and new-key success with status 0. Restore the default promotion policy:

```sh
ceph config rm mon mon_auth_client_pending_key_auto_promote
ceph config get mon mon_auth_client_pending_key_auto_promote
```

Require `true`, active RBD and CephFS storage, and `HEALTH_OK`. After every probe succeeds,
remove the old-key copies on **every node**:

```sh
rm -r /root/cephx-compare
```

Remove the staged keyrings and pending-key JSON once, from the rotation node:

```sh
rm -r <staging>
```

## 9. Replace SSH host keys and root's key

Run on each converted node, one node at a time in a cluster. Use the console or keep the
current session open until fresh access has been verified. Complete and check each stage
before starting the next; retain the old keys until all checks pass. Before starting,
compare the following public-key digest across cluster nodes:

```sh
cut -d' ' -f2 /root/.ssh/id_rsa.pub | sha256sum
```

Require distinct root keys; shared clone keys need review before replacing entries in the
cluster-wide authorized-key file. The following sequence expects existing root RSA and host
RSA/ECDSA key files. New connections can fail between moving the old keys and reloading SSH.

### 9.1 Retain the old keys

`/root/ssh-keys-old` must not exist. If an earlier attempt left it behind, preserve its files
and restore the keys as described in section 9.5 before starting again. Create the directory:

```sh
mkdir -m 0700 /root/ssh-keys-old
```

Stop if `mkdir` fails, including `File exists`. Only after it succeeds, move all six files:

```sh
mv /etc/ssh/ssh_host_rsa_key /etc/ssh/ssh_host_rsa_key.pub /etc/ssh/ssh_host_ecdsa_key /etc/ssh/ssh_host_ecdsa_key.pub /root/.ssh/id_rsa /root/.ssh/id_rsa.pub /root/ssh-keys-old/
```

Require the move to succeed before generating replacements.

### 9.2 Generate and inspect the replacements

```sh
export OPENSSL_CONF=/etc/wolfprovider/openssl.cnf
ssh-keygen -q -t rsa -b 3072 -N '' -f /etc/ssh/ssh_host_rsa_key
ssh-keygen -q -t ecdsa -b 256 -N '' -f /etc/ssh/ssh_host_ecdsa_key
ssh-keygen -q -t rsa -b 4096 -N '' -f /root/.ssh/id_rsa
unset OPENSSL_CONF
ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub; ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub; ssh-keygen -lf /root/.ssh/id_rsa.pub
```

Require all three generation commands to succeed and three fingerprints: 3072-bit RSA,
256-bit ECDSA and 4096-bit RSA, respectively. Stop if any command failed, even if the last
fingerprint command succeeded.

### 9.3 Serve the new host keys

```sh
systemctl reload ssh
systemctl is-active ssh
```

Require a successful reload and `active` before continuing.

### 9.4 Update root authorization

Remove the old root public key and merge the new one, then check both counts:

```sh
grep -vF "$(cut -d' ' -f2 /root/ssh-keys-old/id_rsa.pub)" /etc/pve/priv/authorized_keys > /root/authorized_keys.new; [ $? -le 1 ] && cat /root/authorized_keys.new > /etc/pve/priv/authorized_keys
pvecm updatecerts
grep -cF "$(cut -d' ' -f2 /root/.ssh/id_rsa.pub)" /etc/pve/priv/authorized_keys
grep -cF "$(cut -d' ' -f2 /root/ssh-keys-old/id_rsa.pub)" /etc/pve/priv/authorized_keys
```

Require a successful file update and `pvecm updatecerts`, including `(re)generate node files`
and `merge authorized SSH keys`, followed by counts `1` for the new key and `0` for the old
key. The last count command exits 1 when no old-key entry remains; that is expected.

### 9.5 Verify fresh access

At the console, read the new ECDSA fingerprint:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub
```

On the workstation, repeat [Foundation's host-key refresh](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#5-install-the-packages)
and require a fresh administrative login with the matching fingerprint.

In a cluster, check Proxmox's migration SSH path from this node to every peer and from each
peer back to this node. Replace `<peer>` with the destination node's hostname and `<peer-ip>`
with its address for each direction:

```sh
/usr/bin/ssh -e none -o BatchMode=yes -o HostKeyAlias=<peer> -o UserKnownHostsFile=/etc/pve/nodes/<peer>/ssh_known_hosts -o GlobalKnownHostsFile=none root@<peer-ip> 'echo REMOTE_OK $(hostname)' < /dev/null
```

Require `REMOTE_OK <peer>` in every direction before cleanup or processing another node.
If a check in sections 9.2–9.5 fails, keep the recovery copies. To restore the old keys,
move the six saved files back to their original paths, reload SSH with the command in 9.3,
then run the `pvecm updatecerts` command in 9.4 to restore root authorization.

### 9.6 Remove the old keys

Only after all generation, authorization and access checks pass:

```sh
shred -u /root/ssh-keys-old/ssh_host_rsa_key /root/ssh-keys-old/ssh_host_ecdsa_key /root/ssh-keys-old/id_rsa
rm /root/ssh-keys-old/ssh_host_rsa_key.pub /root/ssh-keys-old/ssh_host_ecdsa_key.pub /root/ssh-keys-old/id_rsa.pub /root/authorized_keys.new
rmdir /root/ssh-keys-old
```

The old Ed25519 host-key files remain on disk but are not served.

## 10. Verify completion and retain recovery material

For standalone nodes, complete the applicable [LUKS unlock and data checks](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-LUKS.md#5-verify-data-and-unlock-after-reboot)
or [ZFS unlock and data checks](../storage-cluster/WOLFSSL-PROXMOX-FIPS-DEPLOYMENT-ZFS.md#5-verify-data-and-unlock-after-reboot),
including the test-file preparation and final reboot, substituting the converted storage names.
Repeat [Foundation verification](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#6-reboot-and-verify)
after that reboot. Require the packaged unlock unit active, the correct encrypted mount,
active storage and successful guest startup. LUKS data and keyslots must use AES-CBC-ESSIV-SHA256
with PBKDF2; ZFS volumes must inherit the new encryption root.

For Ceph, repeat [Ceph verification](../ceph/WOLFSSL-PROXMOX-FIPS-CEPH-DEPLOYMENT.md#5-require-secure-connections-and-verify)
on every node after re-keying. Require all intended daemons, quorum, clean PGs and `HEALTH_OK`.
Complete a serial reboot check using
[Cluster maintenance](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md#5-maintain-and-restart-the-cluster),
including applicable OSD and monitor stop checks. Verify again before proceeding to another
node. After guest volumes have moved, check installer `local-lvm`, if present, on **every node**:

```sh
pvesm list local-lvm
```

Require no volumes on any node, then disable it **once per cluster**:

```sh
pvesm set local-lvm --disable 1
```

Apply [section 6.4's `local` content-list edit](#64-move-remaining-guest-volumes-and-configure-backup-staging)
once per cluster, preserving other types in use,
and complete [Backup destination checks](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md#2-protect-the-destination-and-temporary-files).
Repeat Foundation verification on every node after these changes.

On every path, require **0 FAIL** and apply section 4's warning policy: review `WARN 13`
updates, and resolve other warnings or confirm their stated explanation applies. Only
encrypted destinations may accept guest disks and backups. Compare the volume inventory and
guest data with the pre-conversion records, and test fresh web/API login and SSH access.
Complete a [backup and restore test](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md#4-test-backup-and-restore)
with retained keys. Verifier success alone does not establish key provenance, all volume
placement or recoverability. Use [Node Configuration and Recovery](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md)
for subsequent updates and recovery.

After all acceptance checks pass, restore every HA resource recorded in section 1. On one
Ceph node, run once per `<sid>`, using its original state for `<recorded-state>`:

```sh
ha-manager set <sid> --state <recorded-state>
ha-manager status
```

Read `ha-manager config` again; require each recorded state restored and its service listed
in the status output.

The conversion has these limits:

- The PVE CA, node TLS certificate and existing management-plane keys, including the
  ticket-signing key, can still predate conversion; this procedure does not regenerate them.
- The LUKS header UUID and ZFS pool GUID remain unchanged. They are identifiers, not encryption
  keys. The Ceph cluster identity also remains unchanged.
- Freed blocks from formerly unencrypted volumes can retain plaintext until the media is
  erased. Moving data, deleting volumes or disabling `local-lvm` does not sanitize those blocks.
- The default single-disk installation has no in-place encryption path for `local-lvm` on the
  boot disk. Additional data storage is required for the storage moves; reinstalling is outside
  this guide.
- Boot key files remain on the root filesystem. Keep protected off-node recovery copies;
  storage conversion does not encrypt the root filesystem or external backup destinations.
