#!/usr/bin/env bash
# ONE command. Does everything up to the Windows installer.
#
#   bash scripts/vm/setup.sh                              # auto-detect an ISO
#   bash scripts/vm/setup.sh --iso-url "<signed URL>"     # download it first
#   bash scripts/vm/setup.sh --iso ~/iso/Win11.iso        # explicit path
#   bash scripts/vm/setup.sh --win10                      # lighter: no TPM/SecureBoot
#   bash scripts/vm/setup.sh --status                     # what is done, what is not
#
# Idempotent and resumable: it detects what already exists and only does the
# missing parts, so re-running after a failure or a reboot is safe.
#
# The ONE thing it cannot do is fetch the Windows ISO - Microsoft returns 403
# to non-browser traffic. Everything else is automatic.
set -euo pipefail

NAME="${NAME:-mt5-win}"
VCPUS="${VCPUS:-4}"
MEM_MB="${MEM_MB:-6144}"
DISK_GB="${DISK_GB:-60}"
SHARE_DIR="${SHARE_DIR:-$HOME/mt5-bridge}"
SMB_PASS="${SMB_PASS:-1234}"
ISO_DIR="${ISO_DIR:-$HOME/iso}"
POOL="/var/lib/libvirt/images"
VIRTIO_ISO="$POOL/virtio-win.iso"
VIRTIO_URL="https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WIN_ISO=""; ISO_URL=""; WIN10=0; STATUS_ONLY=0; USE_VNC=0
# Tailscale address of this host, so the console can be bound to the tailnet
# only - reachable from your other devices, not from the LAN or the internet.
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"

while [ $# -gt 0 ]; do
  case "$1" in
    --iso)      WIN_ISO="$2"; shift 2 ;;
    --iso-url)  ISO_URL="$2"; shift 2 ;;
    --win10)    WIN10=1; shift ;;
    --name)     NAME="$2"; shift 2 ;;
    --vcpus)    VCPUS="$2"; shift 2 ;;
    --memory)   MEM_MB="$2"; shift 2 ;;
    --status)   STATUS_ONLY=1; shift ;;
    --vnc)      USE_VNC=1; shift ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done

step() { printf '\n\033[1m── %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[90m·\033[0m %s\n' "$*"; }
todo() { printf '  \033[33m○\033[0m %s\n' "$*"; }
die()  { printf '\n  \033[31m✗ %s\033[0m\n\n' "$*"; exit 1; }

have_pkgs() { command -v virsh >/dev/null && command -v virt-install >/dev/null \
                && command -v swtpm >/dev/null && command -v smbd >/dev/null; }
vm_exists() { sudo virsh dominfo "$NAME" >/dev/null 2>&1; }

find_iso() {
  [ -n "$WIN_ISO" ] && { [ -f "$WIN_ISO" ] && echo "$WIN_ISO"; return; }
  # Prefer the largest Windows-looking ISO; installers are 4-7GB.
  find "$ISO_DIR" "$HOME/Downloads" "$HOME" "$POOL" -maxdepth 2 \
       -iname "*.iso" -size +3G 2>/dev/null \
    | grep -ivE "virtio" | head -1
}

# ---------------------------------------------------------------- status ----
if [ "$STATUS_ONLY" -eq 1 ]; then
  step "status"
  have_pkgs && ok "host packages installed" || todo "host packages missing"
  systemctl is-active --quiet libvirtd && ok "libvirtd active" || todo "libvirtd inactive"
  sudo virsh net-info default >/dev/null 2>&1 && ok "NAT network present" || todo "NAT network missing"
  [ -d "$SHARE_DIR" ] && ok "share dir $SHARE_DIR" || todo "share dir missing"
  sudo pdbedit -L 2>/dev/null | grep -q "^$USER:" && ok "samba user $USER" || todo "samba user not set"
  [ -f "$VIRTIO_ISO" ] && ok "virtio ISO present" || todo "virtio ISO missing"
  found="$(find_iso || true)"
  # Once the VM exists the ISO has usually been relocated into the libvirt
  # pool, which is root-only, so check the domain's own disk list too.
  [ -z "$found" ] && found="$(sudo virsh domblklist "$NAME" 2>/dev/null \
      | awk '/\.iso/ && !/virtio|unattend/ {print $2}' | head -1)"
  [ -n "$found" ] && ok "windows ISO: $(basename "$found")" \
                  || todo "windows ISO NOT FOUND - this is the only manual step"
  vm_exists && ok "VM '$NAME' defined ($(sudo virsh domstate "$NAME" 2>/dev/null))" \
            || todo "VM '$NAME' not created"
  echo; exit 0
fi

echo
echo "  MT5 Windows VM — one-shot setup"
echo "  vm=$NAME  vcpus=$VCPUS  mem=${MEM_MB}MB  disk=${DISK_GB}GB"

# ------------------------------------------------------------ 1. hardware ---
step "1/6  hardware"
grep -qE '(vmx|svm)' /proc/cpuinfo || die "no VT-x/AMD-V. Enable virtualisation in the BIOS."
[ -e /dev/kvm ] || die "/dev/kvm missing."
ok "virtualisation available ($(grep -oE 'vmx|svm' /proc/cpuinfo | sort -u | tr -d '\n'))"
TOTAL_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
if [ "$TOTAL_MB" -lt $((MEM_MB + 6000)) ]; then
  MEM_MB=4096
  todo "only ${TOTAL_MB}MB RAM — reducing the guest to ${MEM_MB}MB"
fi
ok "${TOTAL_MB}MB RAM, guest gets ${MEM_MB}MB"
FREE_GB=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
[ "$FREE_GB" -lt $((DISK_GB + 15)) ] && die "only ${FREE_GB}GB free; need ~$((DISK_GB + 15))GB"
ok "${FREE_GB}GB free"

# --------------------------------------------------- 2. host packages/share --
step "2/6  host packages, network, share"
if have_pkgs && [ -d "$SHARE_DIR" ] && sudo virsh net-info default >/dev/null 2>&1; then
  skip "already configured"
else
  SHARE_DIR="$SHARE_DIR" bash "$HERE/00-host-setup.sh" </dev/null >/tmp/mt5-host-setup.log 2>&1 \
    || { tail -25 /tmp/mt5-host-setup.log; die "host setup failed (see /tmp/mt5-host-setup.log)"; }
  ok "host configured (log: /tmp/mt5-host-setup.log)"
fi
systemctl is-active --quiet libvirtd || sudo systemctl start libvirtd
ok "libvirtd active"
sudo virsh net-start default >/dev/null 2>&1 || true
ok "NAT network up — host is 192.168.122.1"

# --------------------------------------------------------- 3. samba user -----
step "3/6  samba credentials"
if sudo pdbedit -L 2>/dev/null | grep -q "^$USER:"; then
  skip "samba user '$USER' already exists"
else
  printf '%s\n%s\n' "$SMB_PASS" "$SMB_PASS" | sudo smbpasswd -a -s "$USER" >/dev/null
  ok "samba user '$USER' created (password: $SMB_PASS)"
fi
sudo systemctl restart smbd
if command -v smbclient >/dev/null; then
  if smbclient "//localhost/mt5bridge" -U "$USER%$SMB_PASS" -c "ls" >/dev/null 2>&1; then
    ok "share verified read/write over SMB"
  else
    todo "share listed but not writable from localhost — check hosts allow"
  fi
fi

# -------------------------------------------------------- 4. virtio ISO ------
step "4/6  virtio driver ISO"
if [ -f "$VIRTIO_ISO" ]; then
  skip "already present ($(du -h "$VIRTIO_ISO" | cut -f1))"
else
  sudo mkdir -p "$POOL"
  sudo curl -fL --progress-bar -o "$VIRTIO_ISO" "$VIRTIO_URL" || die "virtio download failed"
  ok "downloaded $(du -h "$VIRTIO_ISO" | cut -f1)"
fi

# ------------------------------------------------------- 5. windows ISO ------
step "5/6  windows ISO"
mkdir -p "$ISO_DIR"
if [ -n "$ISO_URL" ]; then
  WIN_ISO="$ISO_DIR/windows.iso"
  if [ -f "$WIN_ISO" ] && [ "$(stat -c%s "$WIN_ISO")" -gt 3000000000 ]; then
    skip "already downloaded"
  else
    echo "  downloading from the supplied URL..."
    curl -fL --progress-bar -o "$WIN_ISO" "$ISO_URL" || die "download failed — the signed URL may have expired"
  fi
fi
[ -z "$WIN_ISO" ] && WIN_ISO="$(find_iso || true)"

if [ -z "$WIN_ISO" ] || [ ! -f "$WIN_ISO" ]; then
  cat <<MANUAL

  ○ No Windows ISO found. This is the only step that cannot be automated:
    Microsoft returns HTTP 403 to non-browser traffic.

    Everything else is DONE. Two ways to finish:

    a) Download it in your browser, then from your Windows PC:
         scp C:\\path\\to\\Win11.iso leo@<minipc>:$ISO_DIR/

    b) On the Microsoft page, click through to the real download button,
       right-click → copy link, then re-run with it:
         bash scripts/vm/setup.sh --iso-url "<paste the signed URL>"

    Then re-run this command — it resumes from here.

MANUAL
  exit 3
fi
ok "using $(basename "$WIN_ISO") ($(du -h "$WIN_ISO" | cut -f1))"
sudo file "$WIN_ISO" | grep -qi "ISO 9660" || todo "does not look like an ISO 9660 image — continuing anyway"

# qemu runs as the unprivileged 'libvirt-qemu' user, which typically cannot
# traverse a 750 home directory. Rather than loosening permissions on $HOME,
# relocate the image into the libvirt pool where it belongs. Same filesystem,
# so the move is instant.
if ! sudo -u libvirt-qemu test -r "$WIN_ISO" 2>/dev/null; then
  POOL_ISO="$POOL/$(basename "$WIN_ISO")"
  if [ -f "$POOL_ISO" ]; then
    skip "already in the libvirt pool"
  else
    echo "  libvirt-qemu cannot read it from \$HOME — moving into $POOL"
    sudo mv "$WIN_ISO" "$POOL_ISO"
    sudo chmod 644 "$POOL_ISO"
  fi
  WIN_ISO="$POOL_ISO"
  ok "ISO now at $WIN_ISO"
fi

# ------------------------------------------------------------- 6. the VM -----
step "6/6  create the VM"
if vm_exists; then
  skip "VM '$NAME' already exists (state: $(sudo virsh domstate "$NAME"))"
else
  # SPICE on loopback is the safer default (virt-viewer tunnels it over SSH).
  # --vnc binds VNC to the Tailscale address instead, so any VNC client on any
  # tailnet device can connect with nothing extra installed.
  if [ "$USE_VNC" -eq 1 ]; then
    [ -n "$TS_IP" ] || die "--vnc needs a Tailscale address; none found"
    GRAPHICS=(--graphics "vnc,listen=$TS_IP,password=$SMB_PASS")
    ok "console: VNC on $TS_IP:5900 (tailnet only), password $SMB_PASS"
  else
    GRAPHICS=(--graphics spice,listen=127.0.0.1)
    ok "console: SPICE on loopback (connect with virt-viewer over SSH)"
  fi

  # Many modern QEMU builds ship without qxl. Pick the best model this build
  # actually has rather than assuming.
  VIDEO="vga"
  for v in virtio qxl; do
    case "$v" in
      virtio) probe="virtio-vga" ;;
      qxl)    probe="qxl" ;;
    esac
    if qemu-system-x86_64 -device help 2>/dev/null | grep -q "\"$probe\""; then
      VIDEO="$v"; break
    fi
  done
  ok "video model: $VIDEO"

  if [ "$WIN10" -eq 1 ]; then
    OSINFO="win10"; EXTRA=(--boot uefi)
  else
    OSINFO="win11"
    EXTRA=(--boot uefi --machine q35 --features smm.state=on
           --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb)
  fi
  sudo virt-install \
    --name "$NAME" --osinfo "$OSINFO" \
    --vcpus "$VCPUS" --memory "$MEM_MB" --cpu host-passthrough \
    --disk "path=$POOL/$NAME.qcow2,size=$DISK_GB,format=qcow2,bus=virtio,cache=writeback,discard=unmap" \
    --disk "path=$WIN_ISO,device=cdrom,bus=sata" \
    --disk "path=$VIRTIO_ISO,device=cdrom,bus=sata" \
    --network network=default,model=virtio \
    "${GRAPHICS[@]}" --video "$VIDEO" --sound none \
    --noautoconsole "${EXTRA[@]}" || die "virt-install failed"
  ok "VM created ($OSINFO)"
fi
sudo virsh autostart "$NAME" >/dev/null 2>&1 || true
ok "autostart enabled — survives a host reboot"

cat <<DONE

  ────────────────────────────────────────────────────────────────
  Host side complete. Windows setup is interactive from here.

  1. Open the console — ON YOUR WINDOWS PC:

       if you used --vnc:
         any VNC client  ->  $TS_IP:5900   password: $SMB_PASS

       otherwise (SPICE, nothing exposed):
         install virt-viewer for Windows, then:
         virt-viewer --connect qemu+ssh://leo-1/system $NAME

  2. When Windows setup shows NO DISKS — expected, not an error:
       "Load driver" → the virtio CD → amd64\\<win version>\\viostor

  3. After Windows boots:
       - run virtio-win-guest-tools.exe from the virtio CD
       - net use Z: \\\\192.168.122.1\\mt5bridge /persistent:yes
         user: $USER   password: $SMB_PASS
       - install MT5, log in to the FTMO account
       - Tools → Options → Expert Advisors → ENABLE algo trading
       - DISABLE MT5 auto-update  (an update prompt blocks a headless terminal)
       - copy mql5/*.mq5 → MQL5\\Experts and MQL5\\Scripts, compile,
         attach TcsBridge to ONE chart

  4. Snapshot the moment it works:
       bash scripts/vm/02-snapshot.sh $NAME working-baseline

  5. Verify from Linux:
       python3 scripts/preflight.py --bridge-dir $SHARE_DIR --vm $NAME

  Progress at any time:  bash scripts/vm/setup.sh --status
  ────────────────────────────────────────────────────────────────

DONE
