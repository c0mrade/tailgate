#!/usr/bin/env python3
"""Cut a release: pick the next version, then bump plugin.yaml, commit, tag and push.

Run from the repository root on an up-to-date, clean `main`:

    python3 scripts/release.py

Pushing the tag starts .github/workflows/release.yml, which runs the tests and publishes the
GitHub Release.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MANIFEST = Path("plugin.yaml")
VERSION_LINE = re.compile(r'^version:\s*"?(\d+)\.(\d+)\.(\d+)"?\s*$', re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def fail(message: str) -> None:
    sys.exit(f"release: {message}")


def current_version() -> tuple[int, int, int]:
    match = VERSION_LINE.search(MANIFEST.read_text(encoding="utf-8"))
    if not match:
        fail(f"no `version: X.Y.Z` line in {MANIFEST}")
    return tuple(int(part) for part in match.groups())


def bumped(version: tuple[int, int, int], part: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if part == "major":
        return major + 1, 0, 0
    if part == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def fmt(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def check_repository() -> None:
    if not MANIFEST.exists():
        fail("run this from the repository root")
    if git("branch", "--show-current") != "main":
        fail("switch to main first")
    if git("status", "--porcelain"):
        fail("the working tree has uncommitted changes, commit or stash them first")
    git("fetch", "--quiet", "--tags", "origin")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        fail("main differs from origin/main, pull or push first")


def ask_version(current: tuple[int, int, int]) -> str:
    options = {str(i): (part, fmt(bumped(current, part)))
               for i, part in enumerate(("patch", "minor", "major"), 1)}
    tags = [t for t in git("tag", "--list", "v*", "--sort=-v:refname").splitlines() if t]
    print(f"Current version: {fmt(current)} (plugin.yaml), latest tag: {tags[0] if tags else 'none'}")
    for key, (part, version) in options.items():
        print(f"  {key}) {part:<5}  {version}")
    print("  or type a version, e.g. 1.0.0")
    answer = input("Release which version? ").strip().removeprefix("v")
    if answer in options:
        return options[answer][1]
    if re.fullmatch(r"\d+\.\d+\.\d+", answer):
        return answer
    fail(f"not an option or an X.Y.Z version: {answer!r}")


def main() -> None:
    check_repository()
    current = current_version()
    version = ask_version(current)
    tag = f"v{version}"
    if git("tag", "--list", tag):
        fail(f"tag {tag} already exists")
    if tuple(map(int, version.split("."))) <= current and version != fmt(current):
        fail(f"{version} is not newer than {fmt(current)}")

    if version != fmt(current):
        text = MANIFEST.read_text(encoding="utf-8")
        MANIFEST.write_text(VERSION_LINE.sub(f"version: {version}", text, count=1), encoding="utf-8")
        git("add", str(MANIFEST))
        git("commit", "--quiet", "-m", f"Release {tag}")
        print(f"Committed plugin.yaml at {version}.")
    git("tag", "-a", tag, "-m", f"tailgate {tag}")
    print(f"Tagged {tag}.")

    if input(f"Push main and {tag} to origin now? [y/N] ").strip().lower() != "y":
        print(f"Not pushed. When ready: git push origin main {tag}")
        return
    git("push", "--quiet", "origin", "main", tag)
    print(f"Pushed. The release workflow now tests {tag} and publishes the GitHub Release.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nrelease: cancelled, nothing pushed")
    except subprocess.CalledProcessError as e:
        fail(f"git {' '.join(e.cmd[1:])} failed: {e.stderr.strip()}")
