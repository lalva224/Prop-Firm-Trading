#!/usr/bin/env python3
"""Type text into a VM console via `virsh send-key`.

Needed to bootstrap a brand-new Windows guest: there is no agent, no SSH and
no shared clipboard yet, so the only input channel is emulated keystrokes.
Used once, to launch a script from the shared folder that installs OpenSSH -
after which everything else happens over a real shell.

    python3 scripts/vm/vmtype.py mt5-win --key LEFTMETA R
    python3 scripts/vm/vmtype.py mt5-win --text 'powershell -ep bypass -f Z:\\go.ps1'
    python3 scripts/vm/vmtype.py mt5-win --key ENTER
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

# Unshifted keys
PLAIN = {
    " ": "SPACE", "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE",
    "\\": "BACKSLASH", ";": "SEMICOLON", "'": "APOSTROPHE", "`": "GRAVE",
    ",": "COMMA", ".": "DOT", "/": "SLASH", "\t": "TAB", "\n": "ENTER",
}
# Characters that need shift, mapped to the physical key
SHIFTED = {
    ":": "SEMICOLON", '"': "APOSTROPHE", "<": "COMMA", ">": "DOT", "?": "SLASH",
    "{": "LEFTBRACE", "}": "RIGHTBRACE", "|": "BACKSLASH", "~": "GRAVE",
    "_": "MINUS", "+": "EQUAL", "!": "1", "@": "2", "#": "3", "$": "4",
    "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
}


def keys_for(ch: str) -> list[str] | None:
    if ch.isdigit():
        return [f"KEY_{ch}"]
    if ch.isalpha() and ch.isascii():
        k = f"KEY_{ch.upper()}"
        return ["KEY_LEFTSHIFT", k] if ch.isupper() else [k]
    if ch in PLAIN:
        return [f"KEY_{PLAIN[ch]}"]
    if ch in SHIFTED:
        return ["KEY_LEFTSHIFT", f"KEY_{SHIFTED[ch]}"]
    return None


def send(domain: str, keys: list[str], delay: float) -> bool:
    cmd = ["sudo", "virsh", "send-key", domain, "--codeset", "linux", *keys]
    r = subprocess.run(cmd, capture_output=True, text=True)
    time.sleep(delay)
    if r.returncode != 0:
        print(f"  send-key failed for {keys}: {r.stderr.strip()[:120]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--text", help="string to type")
    ap.add_argument("--key", nargs="+", help="raw key names, e.g. LEFTMETA R")
    ap.add_argument("--delay", type=float, default=0.06)
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()

    for _ in range(a.repeat):
        if a.key:
            send(a.domain, [f"KEY_{k.upper()}" for k in a.key], a.delay * 3)
        if a.text:
            skipped = []
            for ch in a.text:
                ks = keys_for(ch)
                if ks is None:
                    skipped.append(ch)
                    continue
                send(a.domain, ks, a.delay)
            if skipped:
                print(f"  unmapped characters skipped: {skipped}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
