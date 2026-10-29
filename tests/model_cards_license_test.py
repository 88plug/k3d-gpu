#!/usr/bin/env python3
"""research/model-cards ships third-party Hub files. Each <org>__<name>/ dir must map, in the
README's dir table, to a license text under LICENSES/ that carries the license the dir's model
card declares (or, where the card declares none, the license of its base model). Each license
file must match the sha256 the README's license-file table records for it.

Usage: python3 tests/model_cards_license_test.py
"""

import hashlib
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = "research/model-cards"

# The card's `license:` value (or `license_name:` for `other`) -> the README's License column.
CARD_LICENSE = {"apache-2.0": "Apache-2.0", "mit": "MIT", "qwen-community-1.0": "qwen-community-1.0"}
# The opening a license text has, after CRLF -> LF.
TEXT_HEAD = {
    "Apache-2.0": re.compile(r"\A\s*Apache License\n\s*Version 2\.0, January 2004\n"),
    "MIT": re.compile(r"\AMIT License\n"),
    "qwen-community-1.0": re.compile(r"\AQwen Community License 1\.0\n"),
}
DIR_ROW = re.compile(r"^\| `([^`/]+)/` \| ([^|]+?) \| `(LICENSES/[^`]+)` \|$", re.M)
FILE_ROW = re.compile(r"^\| `(LICENSES/[^`]+)` \| ([^|]+?) \| .+ \| `([0-9a-f]{64})` \|$", re.M)


def card(path):
    """(license, base models) from a model card's YAML front matter."""
    m = re.match(r"---\r?\n(.*?)\r?\n---", path.read_text(encoding="utf-8"), re.S)
    fm = m.group(1) if m else ""
    lic = re.search(r"^license:\s*(\S+)", fm, re.M)
    name = re.search(r"^license_name:\s*(\S+)", fm, re.M)
    base = re.search(r"^base_model:\s*\r?\n((?:- .*\r?\n?)+)", fm, re.M)
    lic = lic and (name.group(1) if lic.group(1) == "other" and name else lic.group(1))
    return lic, re.findall(r"^- (\S+)", base.group(1), re.M) if base else []


def problems(root):
    cards = root / CARDS
    readme = (cards / "README.md").read_text(encoding="utf-8")
    out = []
    index = {line.split()[0].replace("/", "__") for line in (cards / "INDEX.txt").read_text().splitlines() if line.strip()}
    dirs = {p.name for p in cards.iterdir() if p.is_dir() and p.name not in ("probes", "LICENSES")}
    if dirs != index:
        out.append(f"dirs on disk {sorted(dirs ^ index)} differ from INDEX.txt")

    files = {}
    for rel, lic, sha in FILE_ROW.findall(readme):
        files[rel] = lic
        f = cards / rel
        if not f.is_file():
            out.append(f"{rel}: listed but missing")
            continue
        if hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            out.append(f"{rel}: sha256 differs from the README")
        if lic not in TEXT_HEAD or not TEXT_HEAD[lic].match(f.read_text(encoding="utf-8").replace("\r\n", "\n")):
            out.append(f"{rel}: is not the {lic} text")
    on_disk = {f"LICENSES/{p.name}" for p in (cards / "LICENSES").glob("*")} if (cards / "LICENSES").is_dir() else set()
    for rel in sorted(on_disk - set(files)):
        out.append(f"{rel}: not in the README's license-file table")

    rows = {}
    for d, lic, rel in DIR_ROW.findall(readme):
        if d in rows:
            out.append(f"{d}/: more than one row")
        rows[d] = (lic, rel)
    for d in sorted(dirs):
        if d not in rows:
            out.append(f"{d}/: no row in the README's dir table")
            continue
        lic, rel = rows[d]
        if rel not in files:
            out.append(f"{d}/: {rel} is not in the license-file table")
        elif files[rel] != lic:
            out.append(f"{d}/: row says {lic}, {rel} is {files[rel]}")
        declared, base = card(cards / d / "README.md")
        if declared is None and base:
            declared, _ = card(cards / base[0].replace("/", "__") / "README.md")
        if CARD_LICENSE.get(declared) != lic:
            out.append(f"{d}/: card declares {declared}, row says {lic}")
    for d in sorted(set(rows) - dirs):
        out.append(f"{d}/: row for a dir that does not exist")
    return out


class RealRepo(unittest.TestCase):
    def test_every_dir_maps_to_its_license_text(self):
        self.assertEqual(problems(ROOT), [])


class Catches(unittest.TestCase):
    """The check fails on a copy of the real directory with one thing broken."""

    def broken(self, edit):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            shutil.copytree(ROOT / CARDS, root / CARDS)
            edit(root / CARDS)
            return problems(root)

    def edit_readme(self, old, new):
        def edit(cards):
            p = cards / "README.md"
            text = p.read_text(encoding="utf-8")
            self.assertIn(old, text)
            p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return edit

    def test_dir_without_a_row(self):
        out = self.broken(self.edit_readme("| `Intel__qwen3.8-27B-int4-ov/` |", "| `Intel__qwen3.8-27B-int4-ov-x/` |"))
        self.assertIn("Intel__qwen3.8-27B-int4-ov/: no row in the README's dir table", out)

    def test_row_with_the_wrong_license(self):
        out = self.broken(self.edit_readme(
            "| `Qwen__Qwen3.8-Flash-Next/` | qwen-community-1.0 | `LICENSES/qwen-community-1.0.txt` |",
            "| `Qwen__Qwen3.8-Flash-Next/` | Apache-2.0 | `LICENSES/Apache-2.0.txt` |"))
        self.assertIn("Qwen__Qwen3.8-Flash-Next/: card declares qwen-community-1.0, row says Apache-2.0", out)

    def test_license_file_changed(self):
        def edit(cards):
            with open(cards / "LICENSES" / "Apache-2.0.txt", "a", encoding="utf-8") as f:
                f.write("\n")
        self.assertIn("LICENSES/Apache-2.0.txt: sha256 differs from the README", self.broken(edit))

    def test_unlisted_license_file(self):
        def edit(cards):
            (cards / "LICENSES" / "extra.txt").write_text("x\n", encoding="utf-8")
        self.assertIn("LICENSES/extra.txt: not in the README's license-file table", self.broken(edit))

    def test_card_without_license_follows_its_base_model(self):
        self.assertEqual(card(ROOT / CARDS / "Intel__Qwen3.8-27B-q4km-AutoRound" / "README.md"), (None, ["Qwen/Qwen3.8-27B"]))


if __name__ == "__main__":
    sys.exit(unittest.main())
