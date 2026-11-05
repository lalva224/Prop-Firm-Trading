#!/usr/bin/env bash
# Host-side preparation for the Windows/MT5 VM.
#
# Installs KVM + libvirt, verifies hardware virtualisation, and creates the
# shared folder the file bridge uses. Idempotent - safe to re-run.
#
#   bash scripts/vm/00-host-setup.sh
set -euo pipefail

SHARE_DIR="${SHARE_DIR:-$HOME/mt5-bridge}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  [ ok ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }

say "1. hardware checks"
if grep -qE '(vmx|svm)' /proc/cpuinfo; then
  ok "CPU virtualisation extensions present ($(grep -oE 'vmx|svm' /proc/cpuinfo | sort -u | tr '\n' ' '))"
else
  bad "no VT-x/AMD-V. Enable virtualisation in the BIOS and re-run."; exit 1
fi
[ -e /dev/kvm ] && ok "/dev/kvm present" || { bad "/dev/kvm missing"; exit 1; }

TOTAL_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
ok "RAM ${TOTAL_MB}MB total"
[ "$TOTAL_MB" -lt 12000 ] && bad "under 12GB - give the VM 4GB instead of 6GB" || true

FREE_GB=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
ok "disk ${FREE_GB}GB free on /"
[ "$FREE_GB" -lt 80 ] && bad "under 80GB free - the VM wants ~60GB" || true

say "2. packages"
sudo apt-get update -qq
# "qemu-kvm" is a virtual package on newer Ubuntu; the real one is
# qemu-system-x86. Pick whichever this release actually provides.
QEMU_PKG="qemu-system-x86"
apt-cache show "$QEMU_PKG" >/dev/null 2>&1 || QEMU_PKG="qemu-kvm"
ok "using qemu package: $QEMU_PKG"
sudo apt-get install -y --no-install-recommends \
  "$QEMU_PKG" qemu-utils \
  libvirt-daemon-system libvirt-clients virtinst \
  ovmf swtpm swtpm-tools \
  samba samba-common-bin
ok "installed $QEMU_PKG, libvirt, OVMF (UEFI), swtpm (TPM 2.0), samba"

say "3. permissions"
sudo usermod -aG libvirt,kvm "$USER" || true
ok "added $USER to libvirt and kvm groups"
echo "       (log out and back in, or run: newgrp libvirt)"

say "4. default network"
sudo systemctl enable --now libvirtd
if ! sudo virsh net-info default >/dev/null 2>&1; then
  for xml in /usr/share/libvirt/networks/default.xml \
             /etc/libvirt/qemu/networks/default.xml; do
    [ -f "$xml" ] && { sudo virsh net-define "$xml" && break; }
  done
fi
sudo virsh net-start default 2>/dev/null || true
sudo virsh net-autostart default
ok "libvirt 'default' NAT network up and set to autostart"
HOST_IP=$(ip -4 addr show virbr0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || true)
ok "host address on the VM network: ${HOST_IP:-192.168.122.1}"

say "5. shared folder for the file bridge"
mkdir -p "$SHARE_DIR"
chmod 775 "$SHARE_DIR"
ok "created $SHARE_DIR"

if ! grep -q '^\[mt5bridge\]' /etc/samba/smb.conf 2>/dev/null; then
  sudo tee -a /etc/samba/smb.conf >/dev/null <<SMB

# --- MT5 file bridge: shared with the Windows VM only ---
# Bound to the libvirt NAT network, so it is not reachable from the LAN.
[mt5bridge]
   path = $SHARE_DIR
   browseable = yes
   writable = yes
   guest ok = no
   valid users = $USER
   create mask = 0664
   directory mask = 0775
   # 127.0.0.1 is included so the share is testable from the host with
   # smbclient; everything outside these two is denied, so the LAN
   # cannot reach it.
   hosts allow = 192.168.122.0/24 127.0.0.1
   hosts deny = 0.0.0.0/0
SMB
  ok "added [mt5bridge] share to /etc/samba/smb.conf"
else
  ok "[mt5bridge] share already configured"
fi

if [ -t 0 ]; then
  echo
  echo "  Set the Samba password for $USER (Windows uses this to connect):"
  sudo smbpasswd -a "$USER" || true
else
  echo
  echo "  [ TODO ] no terminal available - set the Samba password yourself:"
  echo "           sudo smbpasswd -a $USER"
fi
sudo systemctl enable --now smbd
sudo systemctl restart smbd
ok "smbd running"

say "done"
cat <<NEXT
  Share path on this host : $SHARE_DIR
  From Windows, map       : \\\\${HOST_IP:-192.168.122.1}\\mt5bridge
  Bridge dir for the bot  : $SHARE_DIR

  Next: bash scripts/vm/01-create-vm.sh --windows-iso /path/to/Win11.iso
NEXT
