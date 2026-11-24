"""Used by install.sh: install or update the sealed core in ~/Duo, and set up the user layer.

  python3 scripts/install_core.py <source folder> <duo home>

First install (also an install that still has the old ~/Duo/source layout): copy the source in as the
first core, and start the user layer (an old install keeps its version number). Later installs: a safe
update, the same as `duo update --from <source>`. Exit code 3 means the new core failed its tests and the
old one still runs.
"""
import sys
from pathlib import Path


def main(src, home):
    src, home = Path(src).resolve(), Path(home)
    sys.path.insert(0, str(src))
    from engine import coremgr, userspace
    old_cfg, shared = src / "config.json", home / "config.json"
    if old_cfg.exists() and not shared.exists():          # settings saved in the source folder move to the shared file once
        home.mkdir(parents=True, exist_ok=True)
        shared.write_text(old_cfg.read_text())
    if coremgr.current_dir(home) is None:
        d = coremgr.install(src, home)
        data = userspace.migrate_legacy(home)
        print("Installed Duo core %s in %s" % (coremgr.info(home)["version"], d))
        print("Your Duo is %s. Your features go in %s" % (data["number"], userspace.user_dir(home)))
        return 0
    userspace.migrate_legacy(home)
    r = coremgr.update(src, home)
    print(r["detail"])
    for n in r.get("disabled", []):
        print("Feature %s is off for now. Duo is fixing it in a copy." % n)
    return 3 if r["status"] == "core_failed" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
