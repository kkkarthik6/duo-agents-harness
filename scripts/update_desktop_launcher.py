"""Update unsealed local desktop launchers. Keep old launchers for rollback."""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


def update(home, source, apps, sealed=None):
    home, source = Path(home), Path(source)
    bridge = source / "scripts/desktop_launcher.py"
    changed = []
    for app in apps:
        app = Path(app)
        target = app / "Contents/Resources/_duo/bin/duo"
        if not target.is_file() or target.is_symlink() or target.read_bytes() == bridge.read_bytes():
            continue
        if sealed is None:
            check = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=2", str(app)], capture_output=True, text=True)
            protected = "Sealed Resources=none" not in check.stderr + check.stdout
        else:
            protected = sealed(app)
        if protected:
            print("Desktop bundle needs a full signed update: " + str(app))
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        app_key = hashlib.sha256(str(app).encode()).hexdigest()[:12]
        backup = home / "backups/desktop-launchers" / app_key / ("duo-" + digest + ".py")
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        staged = target.with_name("duo.installing")
        shutil.copy2(bridge, staged)
        staged.chmod(0o755)
        staged.replace(target)
        print("Updated desktop launcher: " + str(app))
        changed.append(str(app))
    return changed


if __name__ == "__main__":
    home, source = map(Path, sys.argv[1:3])
    update(home, source, [Path.home() / "Applications/Duo.app", Path("/Applications/Duo.app")])
