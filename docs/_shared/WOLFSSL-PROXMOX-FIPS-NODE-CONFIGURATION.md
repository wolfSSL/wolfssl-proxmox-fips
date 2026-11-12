# wolfSSL FIPS for Proxmox VE — Node Maintenance and Recovery

Use this guide after [Installation](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md)
and the selected storage guide for routine maintenance. Sections 5 and 6 apply to unused
standalone nodes immediately after Installation, before guest, storage or cluster configuration.
Commands run as root on the Proxmox node unless labeled otherwise. Keep console access
available during updates and recovery.

## 1. Update the installed packages

Plan a maintenance window and confirm that backups and encryption keys are available.
On a cluster, follow the [Cluster guide](../storage-cluster/WOLFSSL-PROXMOX-FIPS-CLUSTER.md)
and update one node at a time, preserving quorum and storage availability.

Refresh the preferences before the general upgrade. APT reads them when each command starts;
installing new preferences in the same transaction as the other packages is too late.

```sh
apt update
apt install wolfssl-fips-pve-apt
```

On encrypted ZFS nodes, replace the install command above with:

```sh
apt install wolfssl-fips-pve-apt wolfssl-fips-pve-apt-zfs
```

Then review and apply the upgrade:

```sh
apt full-upgrade --allow-downgrades
```

`--allow-downgrades` permits compatible wolfSSL rebuilds whose versions sort below the
upstream versions. Use this command instead of `apt upgrade`. Do not accept unexpected
removal of the integration or storage packages. In particular, refreshing both ZFS
preferences first keeps the userspace tools and kernel modules on the same OpenZFS release.

**Important:** Upstream security fixes for pinned components become available on these nodes
only after wolfSSL publishes compatible rebuilds. A newly installed stock Proxmox kernel
does not replace the wolfSSL kernel pinned for boot. Review held packages and verifier
warnings, and contact wolfSSL when a required security update is awaiting a rebuild; do not
remove the preferences to bypass the dependency constraints.

```sh
apt list --upgradable
```

Package installation does not replace every running process. Evacuate or shut down guests
using the cluster's normal maintenance procedure, then reboot:

```sh
reboot
```

## 2. Verify activation

After reboot, wait for `pve-guests` to finish starting the configured guests:

```sh
uname -r
systemctl is-active pve-guests
wolfssl-fips-pve-verify --login
```

The kernel must be the intended `+wolfcrng` release and `pve-guests` must be `active`.
The verifier prompts for the root password to check management login. Resolve every `FAIL`
and assess each warning before returning the node to service. Open a fresh SSH connection
with the ECDSA or RSA key configured during Installation.

Repeat the selected storage guide's unlock, placement and data checks. On a cluster,
confirm `pvecm status` reports `Quorate: Yes`; on Ceph, also require the health and OSD
conditions in its deployment guide before advancing to another node. A verifier pass does
not prove that every backup destination, restored guest or application is correctly configured.

The packaged wrappers cover QEMU, PBS restore and backup clients. They survive ordinary
package updates through dpkg diversions; do not recreate the old manual wrappers or package
holds. If a VM was started while wrappers were disabled during recovery, shut it down fully
and start it again. A reboot inside the guest does not replace its QEMU process.

## 3. Retain a working kernel

Keep the previous wolfSSL kernel as a boot fallback. The preference package retains its
matching wolfCrypt module and, on encrypted ZFS nodes, its wolfZFS modules for as long as
Proxmox retains that kernel. Review `apt autoremove` proposals as a set: kernel and matching
modules must be removed together. Do not remove the running kernel or the last working
fallback.

To boot an installed previous wolfSSL kernel once, replace `<kernel-version>` with its full
version from the installed package list:

```sh
dpkg -l 'proxmox-kernel-*+wolfcrng*'
proxmox-boot-tool kernel pin <kernel-version> --next-boot
reboot
```

The verifier reports the temporary kernel selection. The following reboot returns to the
normal pinned kernel. Use this for diagnosis; a previous release may lack newer security fixes.
For general boot and retention administration, see
[Proxmox boot management](https://pve.proxmox.com/pve-docs/chapter-sysadmin.html#sysboot).

## 4. Recover administrative access

### 4.1 SSH key rejection

An Ed25519 key cannot authenticate under the packaged SSH policy. At the node console,
authorize an ECDSA or RSA public key as described in
[Installation, section 2](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#2-prepare-ssh-access).
Test a fresh connection with that key before closing the console. If the server configuration
was changed, validate it before restarting SSH:

```sh
sshd -t && systemctl restart ssh
```

### 4.2 Provider cannot load

A missing or damaged provider module can disable SSH, management login, corosync and storage
unlock. If `/etc/wolfprovider/openssl.cnf` is missing or empty, these services start on
OpenSSL's default provider, outside wolfCrypt and without error; `wolfssl-fips-pve-verify`
fails. Diagnose at the console before restarting services:

```sh
OPENSSL_CONF=/etc/wolfprovider/openssl.cnf openssl list -providers
OPENSSL_CONF=/etc/wolfprovider/openssl.cnf openssl dgst -sha256 /dev/null
echo $?
```

The listing must show `name: wolfSSL Provider FIPS` with `status: active`, and the digest must
be `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` with exit status `0`.
An empty listing means the module is missing or cannot load, despite exit status `0`.
OpenSSL's default provider in the listing means the configuration file is missing or empty;
the digest still succeeds, so rely on the listing. Keep the `OPENSSL_CONF` prefix:
without it, `openssl` uses OpenSSL's default provider and prints the digest even when
wolfProvider is missing.

If the configuration file is missing or empty, restore it from the package. Move an empty or
edited file aside first so dpkg treats it as missing, then reboot to activate wolfProvider
in every service that started without it:

```sh
[ -e /etc/wolfprovider/openssl.cnf ] && mv /etc/wolfprovider/openssl.cnf /root/openssl.cnf.old
apt install --reinstall -o Dpkg::Options::=--force-confmiss wolfprovider-fips
OPENSSL_CONF=/etc/wolfprovider/openssl.cnf openssl list -providers
```

If `libwolfprov.so` was moved, restore it. If deleted, reinstall the provider on the affected
node. APT does not use wolfProvider and works even when the provider cannot load:

```sh
apt install --reinstall wolfprovider-fips
test -s /etc/wolfprovider/openssl.cnf
```

If the configuration check fails, or the provider still fails to load, stop and obtain
wolfSSL support for package recovery. To regain access meanwhile, mask the SSH, management
interface and corosync drop-ins. These services then use OpenSSL's default provider,
outside wolfCrypt and without error:

```sh
for u in ssh pveproxy corosync; do
  mkdir -p /etc/systemd/system/$u.service.d
  ln -sf /dev/null /etc/systemd/system/$u.service.d/50-wolfssl-fips.conf
done
systemctl daemon-reload
systemctl restart ssh pveproxy
```

Leave corosync running; if it failed to start, `systemctl restart corosync` starts it on the
default provider. Once the provider loads again, remove the three masks before the reboot below:

```sh
rm /etc/systemd/system/{ssh,pveproxy,corosync}.service.d/50-wolfssl-fips.conf
systemctl daemon-reload
```

Removing service drop-ins alone does not restore native package versions. Repair the
installed integration as below; returning a configured node to stock requires
reinstallation. Unused Foundation nodes may use section 6 within its limits.

Once the configuration file exists and the digest check succeeds again, APT can repair the
shared packages. Reinstall provider, library and config in the same transaction; the APT
hook requires config to be installed or included in that transaction:

```sh
apt install --reinstall --allow-downgrades wolfprovider-fips libwolfssl-fips wolfssl-fips-pve-config
test -s /etc/wolfprovider/openssl.cnf
```

Stop if the transaction or configuration check fails. Verify both provider content and a
cryptographic operation:

```sh
OPENSSL_CONF=/etc/wolfprovider/openssl.cnf openssl list -providers
OPENSSL_CONF=/etc/wolfprovider/openssl.cnf openssl dgst -sha256 /dev/null
echo $?
sshd -t
```

Require `name: wolfSSL Provider FIPS`, `status: active`, the digest above, exit status `0`,
and no SSH configuration error. Shut down guests from inside their operating systems if
Proxmox cannot control them, then reboot the node and complete section 2. Confirm encrypted
storage has unlocked before restarting affected guests. Recover cluster nodes serially,
checking quorum before continuing to another node.

This procedure repairs the installed wolfSSL configuration. Returning permanently to stock
Proxmox on a configured node requires reinstallation. Only an unused standalone Foundation
node within [section 6's limits](#6-return-an-unused-foundation-node-to-stock) can use the
coordinated package-removal procedure.

### 4.3 Resume guests after an unlock failure

A failed storage-unlock dependency blocks automatic startup of all guests on the node,
including guests on other storage. Identify the failure:

```sh
systemctl --failed
journalctl -b -u 'wolfzfs-unlock@*' -u 'wolfssl-fips-luks@*'
```

Restore the missing key or correct the mount failure using the selected storage guide,
start its unlock service, and confirm the intended encrypted backing mount. Then start
guests configured for boot:

```sh
pvesh create /nodes/$(hostname)/startall
```

Wait for that task to complete; start any other required guests individually with `qm start`
or `pct start`. Do not use `systemctl start pve-guests`: the unit only permits dependency
activation. It remains inactive until the next boot, so section 2's wait for `active` does
not apply to this recovery session. Run the verifier once guest startup finishes; its
warning about inactive `pve-guests` is expected until the next successful boot.

**Important:** An inactive `pve-guests` will not stop guests during the next shutdown.
Before the next reboot, explicitly stop them and wait for the task to complete:

```sh
pvesh create /nodes/$(hostname)/stopall
qm list
pct list
```

Confirm the guests are stopped before rebooting. After a successful boot, repeat section 2
and the storage guide's unlock checks.

## 5. Change a Ready Foundation node to licensed v5

Use this procedure on an unused standalone node that completed Installation with FIPS Ready,
before creating storage, configuring guests or forming or joining a cluster. Obtain licensed
repository access and the applicable module documentation from wolfSSL first. Keep console
access and the ECDSA or RSA SSH key tested during Installation.

This procedure does not qualify conversion of existing encrypted storage, backup keys or
application data. In particular, Ready's NSS/wolfPKCS11 databases are not converted by the
package upgrade. A node with such data requires a separately reviewed migration before the
libraries are replaced.

Run `wolfssl-fips-pve-verify --login` before changing the source. Require the kernel, provider,
SSH and management-login checks to pass. Only the expected findings described in
[Installation, section 6](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md#6-reboot-and-verify)
for unconfigured storage or Ceph may remain at this stage.

Replace the contents of `/etc/apt/sources.list.d/wolfssl-fips-pve.sources` with the licensed
source below. Preserve `Components: main ceph-tentacle` on a Ceph-prepared node; use `main`
for the other profiles. Keep the existing Debian and Proxmox sources enabled.

```text
Types: deb
URIs: https://packages.wolfssl.com/fips-v5/debian
Suites: pve9
Components: main
Signed-By: /usr/share/keyrings/wolfssl-fips-eval-archive-keyring.gpg FA9AC3974B0099D415C7367319D1F0DB1F304C7B
```

Replace the Ready source rather than adding a second wolfSSL source. Both channels use the
archive key verified during Installation. Do not bypass signature errors. Refresh the indexes
and preferences in a separate transaction:

```sh
apt update
apt install wolfssl-fips-pve-apt
apt update
apt-cache policy wolfssl-fips-pve-libs
```

If the ZFS profile was installed, include `wolfssl-fips-pve-apt-zfs` in the preference-package
install command, as in section 1. Require the library-metapackage candidate to come from the
licensed v5 source at version `1.0.3-1+fipsv5.1` or later. Stop if that candidate is unavailable.
The fixed metapackage selects native Debian libgcrypt and NSS, replacing Ready's library
substitutes without pinning the native libraries to an exact security-update version. Native
libgcrypt and NSS are outside wolfCrypt's validated boundary.

Keep the integration and selected storage metapackages installed. Reinstalling an unchanged
storage metapackage does not upgrade its already-satisfied dependencies. Review and apply a
full upgrade instead:

```sh
apt full-upgrade --allow-downgrades
```

For the standard Foundation installation, removal of the two Ready-only packages
`libgcrypt20-1.11.0-wolfssl` and `libwolfpkcs11` is expected. Proxmox, QEMU, corosync and the
selected storage metapackage must remain installed. Stop if APT proposes removing them or
the wolfSSL integration. Do not bypass a package-removal guard. The Debian, Proxmox and
licensed wolfSSL repositories must remain available throughout the transaction.

After the upgrade succeeds, reboot and verify the running implementation:

```sh
reboot
```

After reconnecting:

```sh
uname -r
cat /proc/sys/crypto/fips_enabled
dpkg-query -W libwolfssl-fips wolfprovider-fips wolfssl-fips-pve-libs
dmesg | grep -F 'wolfCrypt-fips v5.2.4 startup self-test succeeded'
systemctl is-active pve-guests
wolfssl-fips-pve-verify --login
```

Require a kernel containing `+wolfcrng` and ending in `v5-pve`, `fips_enabled` equal to `1`,
and `+fipsv5` in all three package versions. Require the v5 startup self-test success message,
active guest-startup service, and passing kernel, provider, SSH and management-login checks.
Test a fresh SSH connection with the prepared key. The same pre-storage verifier findings
may remain until the selected storage and cluster guides are completed; resolve them before
putting the node into service. The Ready kernel remains available as a fallback.

## 6. Return an unused Foundation node to stock

This procedure applies only to an unused standalone Foundation node on amd64, with GRUB
and no ESPs managed by `proxmox-boot-tool`. The node must have no guests, cluster membership,
configured Ceph or ZFS storage, LUKS devices or encrypted mappings, unlock instances, or custom
NSS/wolfPKCS11 databases. Installer-created unencrypted root and local storage may remain.
An installed stock Proxmox kernel with its image and initrd must be available.

Configured nodes return to stock by reinstallation. Do not delete storage, guest definitions,
keys or databases to make this helper accept a node. Modified package configuration, custom
provider scopes, APT preferences, package holds or kernel pins can also cause refusal and
require review. The helper checks common NSS locations but cannot discover arbitrary
application database paths; confirm that no custom data exists before proceeding.

Keep console access throughout both reboots. Keep the ordinary Debian and Proxmox sources
enabled and authenticated. Install the removal helper from the selected wolfSSL source:

```sh
apt update
apt install wolfssl-fips-pve-apt
dpkg-query -W wolfssl-fips-pve-apt
```

Require version `1.0.19-1` or later. Review the coordinated native replacement and removal plan:

```sh
wolfssl-fips-pve-remove --plan
```

The plan must retain all native Proxmox packages and replace the wolfSSL rebuilds with
authenticated Debian or Proxmox packages. A refusal must be resolved before proceeding;
do not substitute a broad `apt remove` or `apt purge`. The plan is a simulation, so the helper
checks the actual transaction again before package changes.

Prepare a one-time boot of the verified stock kernel with FIPS disabled:

```sh
wolfssl-fips-pve-remove --prepare-boot
```

Only after preparation succeeds, reboot:

```sh
reboot
```

After the console reboot, confirm the selected native kernel and disabled FIPS state:

```sh
uname -r
if [ -e /proc/sys/crypto/fips_enabled ]; then
    cat /proc/sys/crypto/fips_enabled
else
    echo 'fips_enabled is unavailable; require helper validation of the native kernel.'
fi
```

The kernel must match the stock version reported by the helper and contain no `+wolfcrng`;
`fips_enabled` must be `0` when present. Some native Proxmox kernels do not expose that file.
Accept its absence only when the helper validates the running native kernel's installed image
and configuration and confirms that FIPS is disabled. An absent file alone is insufficient;
a present invalid value is refused. This boot still has the integration's userspace packages
installed. Review a new plan and apply it only after these checks succeed:

```sh
wolfssl-fips-pve-remove --plan
wolfssl-fips-pve-remove --apply
```

Read the displayed plan and confirm interactively. The helper restores native packages in
the same transaction that purges the enumerated integration packages and non-running FIPS
kernels and modules. It preserves Proxmox packages, keys and data. It retains the Foundation
source as `/etc/apt/sources.list.d/wolfssl-fips-pve.sources.disabled` and keeps root-only
transaction records under `/var/lib/wolfssl-fips-pve-remove/`. It also removes its temporary
boot override and restores normal stock-kernel selection.

If the helper reports an APT error or incomplete postcondition, stop and retain its error and
transaction record for support. A failed package transaction is not an automatic rollback.
After successful completion, reboot again:

```sh
reboot
```

After reconnecting, check native packages and services:

```sh
uname -r
if [ -e /proc/sys/crypto/fips_enabled ]; then
    cat /proc/sys/crypto/fips_enabled
else
    echo 'fips_enabled is unavailable; require helper validation of the native kernel.'
fi
cat /proc/cmdline
dpkg-query -W proxmox-ve proxmox-default-kernel pve-manager qemu-server
dpkg-query -W apt openssl libssl3t64 libgcrypt20 libnss3
dpkg --audit
openssl list -providers
systemctl is-active ssh pveproxy pvedaemon pve-cluster pve-guests
apt update
apt -s full-upgrade
```

Require the same stock kernel validated before apply, without `+wolfcrng`. Where present,
`fips_enabled` must be `0`; where absent, require the already validated native image and
configuration. The last `fips=` argument on the kernel command line must be `0`, or no such
argument may be present. Require installed Proxmox metapackages and management components, no
audit findings, the active OpenSSL default provider, and active services. The listed APT and
cryptographic-library versions must contain no `wolf` build suffix. The stock update plan
must not remove Proxmox packages or reintroduce wolfSSL packages. Test a fresh SSH connection
and a new Proxmox web-management login. Keep the disabled source and transaction record
until these checks pass.
