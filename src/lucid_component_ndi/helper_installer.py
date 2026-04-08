"""One-time installer for the NDI helper daemon.

Run as: sudo lucid-ndi-helper-installer --display-user <username>

Creates the systemd service, drop-in for agent-core dependency, and starts it.
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

HELPER_SERVICE_NAME = "lucid-ndi-helper.service"
UNIT_DEST = Path(f"/etc/systemd/system/{HELPER_SERVICE_NAME}")
DROPIN_DIR = Path("/etc/systemd/system/lucid-agent-core.service.d")
DROPIN_FILE = DROPIN_DIR / "ndi-helper.conf"

DROPIN_CONTENT = f"""\
[Unit]
Wants={HELPER_SERVICE_NAME}
After={HELPER_SERVICE_NAME}
"""


def install_once(display_user: str) -> int:
    if os.geteuid() != 0:
        print("ERROR: must run as root (sudo)", file=sys.stderr)
        return 1

    # Validate user exists and get UID
    try:
        pw = pwd.getpwnam(display_user)
        uid = pw.pw_uid
    except KeyError:
        print(f"ERROR: user '{display_user}' does not exist", file=sys.stderr)
        return 1

    # Ensure lucid group exists and display_user is a member
    try:
        subprocess.run(["usermod", "-aG", "lucid", display_user], check=True)
        print(f"Added '{display_user}' to 'lucid' group")
    except Exception:
        print(f"WARNING: could not add '{display_user}' to lucid group")

    # Find and substitute the service template
    template_dir = Path(__file__).parent / "systemd"
    template = template_dir / "lucid-ndi-helper.service"
    if not template.exists():
        print(f"ERROR: template not found at {template}", file=sys.stderr)
        return 1

    unit_text = template.read_text()
    unit_text = unit_text.replace("__DISPLAY_USER__", display_user)
    unit_text = unit_text.replace("__DISPLAY_UID__", str(uid))

    # Write systemd unit
    UNIT_DEST.write_text(unit_text)
    os.chmod(str(UNIT_DEST), 0o644)
    print(f"Installed: {UNIT_DEST}")

    # Write drop-in for agent-core dependency
    DROPIN_DIR.mkdir(parents=True, exist_ok=True)
    DROPIN_FILE.write_text(DROPIN_CONTENT)
    print(f"Installed drop-in: {DROPIN_FILE}")

    # Reload and start
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", HELPER_SERVICE_NAME], check=True)
    subprocess.run(["systemctl", "start", HELPER_SERVICE_NAME], check=True)
    print(f"Service {HELPER_SERVICE_NAME} enabled and started")
    return 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Install NDI helper daemon")
    parser.add_argument("--display-user", required=True,
                        help="Username of the X/Wayland session owner")
    args = parser.parse_args()
    sys.exit(install_once(args.display_user))


if __name__ == "__main__":
    main()
