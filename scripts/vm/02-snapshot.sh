#!/usr/bin/env bash
# Snapshot / restore / list for the Windows VM.
#
# This is the rollback capability that MT5-under-Wine cannot give you: take a
# snapshot before every MT5 update or EA change, and revert in seconds if it
# breaks. Do it the moment the VM first works correctly.
#
#   bash scripts/vm/02-snapshot.sh mt5-win working-baseline   # create
#   bash scripts/vm/02-snapshot.sh mt5-win --list
#   bash scripts/vm/02-snapshot.sh mt5-win --revert working-baseline
set -euo pipefail

NAME="${1:?usage: $0 <vm-name> <snapshot-name> | --list | --revert <name>}"
shift

case "${1:-}" in
  --list)
    sudo virsh snapshot-list "$NAME"
    ;;
  --revert)
    SNAP="${2:?snapshot name required}"
    echo "reverting $NAME to '$SNAP' - the VM will be stopped first"
    sudo virsh destroy "$NAME" 2>/dev/null || true
    sudo virsh snapshot-revert "$NAME" "$SNAP" --running
    echo "reverted and restarted"
    ;;
  --delete)
    SNAP="${2:?snapshot name required}"
    sudo virsh snapshot-delete "$NAME" "$SNAP"
    ;;
  "")
    echo "usage: $0 <vm-name> <snapshot-name> | --list | --revert <name> | --delete <name>"
    exit 2
    ;;
  *)
    SNAP="$1"
    # Memory state is included so a revert returns to a running terminal with
    # the EA already attached, rather than to a cold boot.
    sudo virsh snapshot-create-as "$NAME" "$SNAP" \
      --description "taken $(date -Iseconds)" --atomic
    echo "snapshot '$SNAP' created"
    sudo virsh snapshot-list "$NAME"
    ;;
esac
