# wolfSSL FIPS for Proxmox VE — NFS Storage

This guide assumes a Kerberos-enabled export for Proxmox VM disks or file backups.
The wolfSSL client profile uses NFSv4.2 over TCP with `sec=krb5p` to protect file data
in transit. The filer handles server-side cryptography. Verify encryption at rest on the filer.
Container migration, backup and restore require separate qualification.

## 1. Prepare every client

Select `wolfssl-fips-pve-nfs` during [Installation](../_foundation/WOLFSSL-PROXMOX-FIPS-FOUNDATION.md)
on each node that uses the export. The installation reboot activates the profile.
In a cluster, Proxmox shares the storage definition. Each assigned node still needs
its own machine credentials in a root-only `/etc/krb5.keytab` and the site's Kerberos
and NFS idmap settings. The client and filer need compatible AES session-key enctypes.
The export must require `krb5p` and authorize the file operations Proxmox performs as root.

Once the credentials are in place, start `rpc.gssd` as root on each assigned node.
The package checks the Kerberos backend, wolfProvider and kernel cryptography before the daemon starts.

```sh
systemctl start rpc-gssd
```

## 2. Configure the Proxmox storage

For an existing Proxmox NFS definition, check that the server name matches the NFS service
principal and the mount options include `vers=4.2,proto=tcp,sec=krb5p`. If a mount needs
new options, apply them during your usual storage maintenance window.

On a new cluster, add shared storage after the [nodes have joined](WOLFSSL-PROXMOX-FIPS-CLUSTER.md#2-create-and-verify-the-cluster).
This example adds a Proxmox storage definition for VM disks.

```sh
pvesm add nfs <nfs-storage> --server <server-fqdn> --export <export-path> \
  --content images --options vers=4.2,proto=tcp,sec=krb5p
```

Use `backup` for file archives or `images,backup` for both. When creating, migrating or
restoring a VM, check that every disk, including EFI and TPM state, lands on storage with
the required encryption at rest. For file backups, check the archive destination.
See [Migration acceptance](../_migration/WOLFSSL-PROXMOX-FIPS-MIGRATION-ACCEPTANCE.md)
and [Backup and restore](../_shared/WOLFSSL-PROXMOX-FIPS-BACKUP.md) for those workflows.

## 3. Verify access on every assigned node

Run the NFS verifier as root on each node that uses the storage.

```sh
wolfssl-fips-pve-nfs-verify <nfs-storage>
```

A pass confirms that the running `rpc.gssd` uses the wolfSSL integration and the active
mount matches the configured server, export and required options. The verifier is read-only.
Use your usual write/read check on the verified export to confirm Proxmox can store and retrieve data.

After the next planned reboot, confirm that the node regains access with its machine credentials
and rerun the verifier. If `rpc.gssd` fails to start, check `journalctl -b -u rpc-gssd` for the
failed prerequisite. For credential changes or service recovery, see [Node maintenance](../_shared/WOLFSSL-PROXMOX-FIPS-NODE-CONFIGURATION.md).
