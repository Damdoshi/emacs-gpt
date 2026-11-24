#!/usr/bin/python3
"""Build the architecture-independent package without root or build downloads."""
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parent.parent
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1795521600"))
    with tempfile.TemporaryDirectory(prefix="chatgpt-web-deb-") as temporary:
        stage = Path(temporary) / "root"

        def copy(source, destination, executable=False):
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / source, target)
            target.chmod(0o755 if executable else 0o644)

        copy("bin/chatgpt-web", "usr/bin/chatgpt-web", True)
        for path in sorted((root / "chatgpt_web").iterdir()):
            if path.suffix in {".py", ".js", ".json"}:
                copy(str(path.relative_to(root)), "usr/lib/chatgpt-web/chatgpt_web/" + path.name)
        copy("emacs/chatgpt-web.el", "usr/share/emacs/site-lisp/chatgpt-web/chatgpt-web.el")
        copy("debian/50chatgpt-web.el", "etc/emacs/site-start.d/50chatgpt-web.el")
        for source in ["README.fr.md", "doc/protocol.md", "doc/validation.md", "debian/copyright"]:
            copy(source, "usr/share/doc/emacs-chatgpt-web/" + Path(source).name)
        for source, destination in [
            ("doc/chatgpt-web.1", "usr/share/man/man1/chatgpt-web.1.gz"),
            ("debian/changelog", "usr/share/doc/emacs-chatgpt-web/changelog.Debian.gz")]:
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress((root / source).read_bytes(), mtime=0))
        control = stage / "DEBIAN"
        control.mkdir()
        installed_size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file()) // 1024 + 1
        (control / "control").write_text(
            "Package: emacs-chatgpt-web\nVersion: 0.1.0-1\nSection: editors\nPriority: optional\n"
            "Architecture: all\nMaintainer: ChatGPT Web contributors <maintainers@example.invalid>\n"
            "Depends: python3 (>= 3.10), emacs-nox (>= 28.1) | emacs (>= 28.1), chromium, git\n"
            f"Installed-Size: {installed_size}\n"
            "Description: Personal ChatGPT website interface for terminal Emacs\n"
            " Dedicated mode and Python/Chromium companion, without ELPA or an API key.\n"
            " Website compatibility and terminal authentication are experimental.\n")
        (control / "conffiles").write_text("/etc/emacs/site-start.d/50chatgpt-web.el\n")
        for directory in [stage, *[p for p in stage.rglob("*") if p.is_dir()]]:
            directory.chmod(0o755)
        for path in [stage, *stage.rglob("*")]:
            os.utime(path, (epoch, epoch))
        target = dist / "emacs-chatgpt-web_0.1.0-1_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-z9", "--build", str(stage), str(target)], check=True)
        print(target)


if __name__ == "__main__":
    main()
