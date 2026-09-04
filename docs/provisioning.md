# Rebuilding and provisioning guests

Download the current x86_64 cloud images and verify their vendor checksums before
putting them under `~/VMs/`:

- Fedora Rawhide: the `Fedora-Cloud-Base-Generic-Rawhide-*.x86_64.qcow2` image
  and matching `CHECKSUM` from the [Rawhide Cloud image directory](https://download.fedoraproject.org/pub/fedora/linux/development/rawhide/Cloud/x86_64/images/).
  Save a writable copy as `fedora-cloud-rawhide.qcow2` and install the desktop
  environment there before creating the GNOME baseline.
- CentOS Stream 9: [GenericCloud latest](https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2)
  and its [SHA-256 file](https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2.SHA256SUM),
  saved as `centos9-stream-base.qcow2` after verification.
- CentOS Stream 10: [GenericCloud latest](https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2)
  and its [SHA-256 file](https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2.SHA256SUM),
  saved as `centos10-stream-base.qcow2` after verification.

Fedora's `CHECKSUM` is GPG-signed, so verify the signature as well. The CentOS
`SHA256SUM` files sit next to the images on the same host and only catch
transfer corruption, not a compromised mirror.

The headless `nm-rawhide` domain expects an already-prepared
`fedora-rawhide-base.qcow2`; the tracked NoCloud data initializes only the GNOME
Rawhide and CentOS images.
Before defining the cloud-image domains, replace `/home/user/...` in their XML
files and both copies of the dummy ssh key in each `vm/seed*/user-data`.

```sh
virsh -c qemu:///system net-define vm/nmtest-network.xml && virsh -c qemu:///system net-autostart nmtest && virsh -c qemu:///system net-start nmtest
virsh -c qemu:///system define vm/nm-rawhide.xml   # expects a prepared fedora-rawhide-base.qcow2 in ~/VMs
genisoimage -output ~/VMs/seed-gnome.iso -volid cidata -joliet -rock vm/seed/user-data vm/seed/meta-data
virsh -c qemu:///system define vm/nm-rawhide-gnome.xml
# CentOS Stream 9:
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos9-stream-base.qcow2 ~/VMs/nm-c9s.qcow2 30G
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c9s.iso vm/seed-c9s/user-data vm/seed-c9s/meta-data
virsh -c qemu:///system define vm/nm-c9s.xml
# CentOS Stream 10 (use xorriso if genisoimage is unavailable):
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos10-stream-base.qcow2 ~/VMs/nm-c10s.qcow2 30G
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c10s.iso vm/seed-c10s/user-data vm/seed-c10s/meta-data
virsh -c qemu:///system define vm/nm-c10s.xml
```

Cloud-init injects the root ssh key and mounts the read-only `nmsrc` virtiofs
export at `/mnt/nmsrc`. `testvm up` waits for both ssh and cloud-init on these
seed-managed domains. On a fresh guest, install deps and run a build before
taking the baseline snapshot:

```sh
testvm -d nm-c9s up
TESTVM_DOMAIN=nm-c9s nm-vm deps
TESTVM_DOMAIN=nm-c9s nm-vm build
testvm -d nm-c9s snapshot baseline-known-good
```

`nm-vm deps` is safe to rerun. On Fedora it uses DNF5; on Stream 9/10 it enables
CRB, installs a pinned `libndp` pair from CentOS Koji, then pulls the rest via
`builddep`. The Koji RPMs are unsigned, so the script checks them against
SHA-256 hashes committed in `bin/nm-vm`. Run `nm-vm reconf` if Meson was
configured before the dependencies were installed.

NoCloud applies `mounts` and `runcmd` once per instance. Rebuilding a seed ISO
with the same `instance-id` does not update an initialized disk or snapshot; use
a fresh overlay or change the seed's instance ID, then recreate the baseline
after `virsh snapshot-delete`-ing the old one; duplicate snapshot names fail.
