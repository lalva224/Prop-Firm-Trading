#!/usr/bin/env bash
# Create the Windows VM that will run MT5 natively.
#
# Defaults are sized for a 16-core / 18GB host: 4 vCPU and 6GB to the guest,
# leaving 12 cores and ~12GB for research on the Linux side.
#
#   bash scripts/vm/01-create-vm.sh --windows-iso ~/iso/Win11.iso
#   bash scripts/vm/01-create-vm.sh --windows-iso ... --win10   # lighter, no TPM
set -euo pipefail

NAME="${NAME:-mt5-win}"
VCPUS="${VCPUS:-4}"
MEM_MB="${MEM_MB:-6144}"
DISK_GB="${DISK_GB:-60}"
POOL="${POOL:-/var/lib/libvirt/images}"
VIRTIO_URL="https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
WIN_ISO=""; WIN10=0

while [ $# -gt 0 ]; do
  case "$1" in
    --windows-iso) WIN_ISO="$2"; shift 2 ;;
    --win10)       WIN10=1; shift ;;
    --name)        NAME="$2"; shift 2 ;;
    --vcpus)       VCPUS="$2"; shift 2 ;;
    --memory)      MEM_MB="$2"; shift 2 ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
done

[ -n "$WIN_ISO" ] || { echo "ERROR: --windows-iso is required"; exit 2; }
[ -f "$WIN_ISO" ] || { echo "ERROR: not found: $WIN_ISO"; exit 2; }

# The virtio driver ISO is needed DURING Windows setup: the installer cannot
# see a virtio disk without it, and the disk is virtio because IDE/SATA
# emulation is markedly slower.
VIRTIO_ISO="$POOL/virtio-win.iso"
if [ ! -f "$VIRTIO_ISO" ]; then
  echo "downloading virtio-win drivers..."
  sudo curl -fL --progress-bar -o "$VIRTIO_ISO" "$VIRTIO_URL"
fi

if sudo virsh dominfo "$NAME" >/dev/null 2>&1; then
  echo "ERROR: a VM named '$NAME' already exists."
  echo "  remove it with: sudo virsh destroy $NAME; sudo virsh undefine $NAME --nvram --remove-all-storage"
  exit 1
fi

# Windows 11 requires UEFI + Secure Boot + TPM 2.0. Windows 10 needs none of
# that, so --win10 drops the firmware complexity entirely.
if [ "$WIN10" -eq 1 ]; then
  OSINFO="win10"
  EXTRA=(--boot uefi)
else
  OSINFO="win11"
  EXTRA=(--boot uefi
         --machine q35
         --features smm.state=on
         --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb)
fi

echo
echo "creating VM '$NAME': ${VCPUS} vCPU, ${MEM_MB}MB RAM, ${DISK_GB}GB disk, $OSINFO"
echo

sudo virt-install \
  --name "$NAME" \
  --osinfo "$OSINFO" \
  --vcpus "$VCPUS" \
  --memory "$MEM_MB" \
  --cpu host-passthrough \
  --disk "path=$POOL/$NAME.qcow2,size=$DISK_GB,format=qcow2,bus=virtio,cache=writeback,discard=unmap" \
  --disk "path=$WIN_ISO,device=cdrom,bus=sata" \
  --disk "path=$VIRTIO_ISO,device=cdrom,bus=sata" \
  --network network=default,model=virtio \
  --graphics spice,listen=127.0.0.1 \
  --video qxl \
  --sound none \
  --noautoconsole \
  "${EXTRA[@]}"

sudo virsh autostart "$NAME"
echo
cat <<NEXT
VM created and set to autostart with the host.

Connect to the console to run Windows setup:
  virt-viewer --connect qemu:///system $NAME
  # or: sudo virsh domdisplay $NAME   (then point any SPICE client at it)

DURING WINDOWS SETUP, when no disk is listed:
  "Load driver" -> browse the virtio CD -> amd64\\<your windows version>\\viostor
  The disk then appears. This step is expected, not an error.

AFTER WINDOWS IS INSTALLED:
  1. Run virtio-win-guest-tools.exe from the virtio CD (network + balloon drivers)
  2. Map the bridge share:  net use Z: \\\\192.168.122.1\\mt5bridge /persistent:yes
  3. Install MT5, log in to the FTMO account
  4. Tools > Options > Expert Advisors: enable algo trading
  5. DISABLE MT5 auto-update
  6. Copy mql5/*.mq5 into MQL5\\Experts and MQL5\\Scripts, compile, attach TcsBridge
  7. Snapshot:  bash scripts/vm/02-snapshot.sh $NAME working-baseline
NEXT
