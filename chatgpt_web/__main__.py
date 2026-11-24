"""Companion command line. Passwords are only accepted interactively or on stdin."""

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from .actions import apply_patch
from .bridge import Bridge
from .errors import ClientError
from .web import Website, default_profile


def website(args):
    return Website(args.profile, executable=args.browser, headed=args.headed,
                   selector_file=args.selectors, timeout=args.timeout)


async def authenticate(args):
    client = website(args)
    try:
        state = await client.auth_begin()
        while True:
            print("\n" + state["url"] + "\n" + state["title"] + "\n" + state["text"])
            if state["connected"]:
                print("\nSession détectée. Vous pouvez quitter et ouvrir Emacs.")
            if state["challenge"]:
                print("\nVérification visuelle détectée. Ce programme ne la contourne pas."
                      " Une authentification interactive avec affichage peut être nécessaire.")
            controls = state["controls"]
            for index, control in enumerate(controls, 1):
                print(f"{index:2d}. {control['kind']:6s} {control['label']}")
            choice = input("Numéro, r pour actualiser, q pour quitter : ").strip()
            if choice.lower() == "q":
                return
            if choice.lower() == "r":
                state = await client.auth_state()
                continue
            try:
                index = int(choice) - 1
                if index < 0:
                    raise IndexError
                control = controls[index]
            except (ValueError, IndexError):
                print("Choix invalide.")
                continue
            value = None
            if control["kind"] in {"input", "select"}:
                if control["input_type"] in {"checkbox", "radio"}:
                    value = input("Cocher ? [o/N] ").lower() == "o"
                elif control["input_type"] == "email":
                    value = input(control["label"] + " : ")
                else:
                    value = getpass.getpass(control["label"] + " (saisie masquée) : ")
            state = await client.auth_action(control["ref"], value)
            value = None
    finally:
        await client.close()


async def doctor(args):
    result = {"version": __version__, "python": sys.version.split()[0],
              "chromium": shutil.which(args.browser or "chromium"),
              "emacs": shutil.which("emacs"), "git": shutil.which("git"),
              "profile": str(Path(args.profile or default_profile()).expanduser()),
              "display_required": False}
    if args.probe:
        client = website(args)
        try:
            await client.start()
            result["browser_started"] = True
            snapshot = await client.open()
            result["website"] = {"prompt": snapshot["prompt"], "challenge": snapshot["challenge"],
                                 "url": snapshot["url"]}
        except ClientError as error:
            result["error"] = error.as_dict()
        finally:
            await client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if "error" in result or not result["chromium"] else 0


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Compagnon ChatGPT Web pour Emacs en terminal")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--profile", help="Profil Chromium privé et persistant")
    parser.add_argument("--browser", help="Exécutable Chromium")
    parser.add_argument("--selectors", help="Surcharges JSON des sélecteurs du site")
    parser.add_argument("--headed", action="store_true", help="Ouvrir Chromium avec affichage, pour une connexion interactive facultative")
    parser.add_argument("--timeout", type=float, default=600, help="Délai maximal de suivi d'une réponse, en secondes")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bridge", help="Protocole JSON-lines pour Emacs")
    commands.add_parser("auth", help="Connexion interactive dans le terminal")
    diagnostic = commands.add_parser("doctor", help="Diagnostic sans identifiants")
    diagnostic.add_argument("--probe", action="store_true", help="Tester Chromium et le chargement HTTPS du site")
    patch = commands.add_parser("apply-patch", help="Vérifier puis appliquer un patch choisi explicitement")
    patch.add_argument("file")
    patch.add_argument("--directory", required=True)
    patch.add_argument("--strip", type=int)
    patch.add_argument("--check", action="store_true", help="Vérifier seulement")
    args = parser.parse_args(argv)
    try:
        if args.command == "bridge":
            asyncio.run(Bridge(website(args)).run())
        elif args.command == "auth":
            asyncio.run(authenticate(args))
        elif args.command == "doctor":
            return asyncio.run(doctor(args))
        else:
            result = apply_patch(args.file, args.directory, strip=args.strip, check_only=args.check)
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except ClientError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
    except (OSError, ValueError):
        print("Opération impossible. Vérifiez les fichiers, les droits et la configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
