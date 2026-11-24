"""Explicit local patch application. No shell, hooks, staging or commits."""

import os
from pathlib import Path
import re
import shutil
import subprocess

from .errors import ClientError


def apply_patch(path, directory, *, strip=None, check_only=False):
    directory = Path(directory).expanduser().resolve(strict=True)
    patch = Path(path).expanduser().resolve(strict=True)
    if not directory.is_dir() or not patch.is_file():
        raise ClientError("patch_path", "Le dossier de travail ou le patch est indisponible.")
    if not shutil.which("git"):
        raise ClientError("git_missing", "Le programme git est nécessaire pour vérifier et appliquer les patches.")
    env = dict(os.environ)
    # Do not inherit a caller's alternate repository, worktree or index.
    for name in list(env):
        if name.startswith("GIT_"):
            env.pop(name)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    common = ["git", "-c", "core.hooksPath=/dev/null", "-c", "apply.whitespace=nowarn"]
    root = subprocess.run(common + ["rev-parse", "--show-toplevel"], cwd=directory,
                          env=env, capture_output=True, text=True)
    if root.returncode == 0 and Path(root.stdout.strip()).resolve() != directory:
        raise ClientError("repository_root", "Choisissez la racine du dépôt comme dossier de travail pour appliquer ce patch.")
    if strip is None:
        content = patch.read_text(encoding="utf-8")
        strip = 1 if re.search(r"^diff --git |^--- a/", content, re.MULTILINE) else 0
    if not isinstance(strip, int) or not 0 <= strip <= 20:
        raise ClientError("patch_strip", "Le nombre de composants à retirer doit être compris entre 0 et 20.")
    command = common + ["apply", "-p" + str(strip), "--whitespace=nowarn"]
    checked = subprocess.run(command + ["--check", "--", str(patch)], cwd=directory,
                             env=env, capture_output=True, text=True)
    if checked.returncode:
        raise ClientError("patch_check", "Le patch ne peut pas être appliqué :\n" + checked.stderr.strip())
    if check_only:
        return {"checked": True, "directory": str(directory), "strip": strip}
    applied = subprocess.run(command + ["--", str(patch)], cwd=directory,
                             env=env, capture_output=True, text=True)
    if applied.returncode:
        raise ClientError("patch_apply", "L'application du patch a échoué :\n" + applied.stderr.strip())
    return {"applied": True, "directory": str(directory), "strip": strip}
