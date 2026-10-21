#!/usr/bin/env python3
"""The Publish workflow's decisions: the latest release, the next version from the commits since it, whether there is
anything to publish, and the change log as release notes (Markdown) and as the Steam change note.

    python3 publish.py latest RELEASES
    python3 publish.py version --commits FILE [--since TAG]
    python3 publish.py plan --built FILE --files FILE --next VERSION [--since TAG --released FILE]
    python3 publish.py notes --out FILE --commits FILE --changes FILE --files FILE [--notes TEXT]
    python3 publish.py steam --notes FILE --version VERSION --url URL --out FILE

Step outputs go to the file $GITHUB_OUTPUT names; plan sets MODE, TAG and VERSION for the later steps in $GITHUB_ENV.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import build

TAG = re.compile(r"v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))")
# Conventional Commits 1.0.0: type(scope)!: description. Types aren't case-sensitive, BREAKING CHANGE is.
SUBJECT = re.compile(r"(?P<type>[A-Za-z][A-Za-z0-9-]*)(?:\((?P<scope>[^()\r\n]*)\))?(?P<bang>!)?: (?P<description>\S.*)")
BREAKING = re.compile(r"BREAKING[ -]CHANGE: ?(?P<text>.*)")
FOOTER = re.compile(r"(?:[A-Za-z][A-Za-z0-9-]*|BREAKING CHANGE)(?:: | #)")
LEVELS = ["none", "patch", "minor", "major"]
TYPES = {"feat": "minor", "fix": "patch"}
# Lists longer than this are folded in the release notes.
FOLD = 10
# k_cchPublishedDocumentChangeDescriptionMax in the Steamworks SDK.
STEAM_NOTE_MAX = 8000
PAGE_FIELDS = ("title", "description", "visibility")
ITEM_URL = build.ITEM_URL


class Failure(Exception):
    pass


def write(variable, values):
    with open(os.environ[variable], "a", encoding="utf-8") as out:
        for key, value in values.items():
            out.write(f"{key}={value}\n")


def output(**values):
    write("GITHUB_OUTPUT", values)


def git(*args, check=True):
    run = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and run.returncode != 0:
        raise Failure(f"git {' '.join(args)} failed: {run.stderr.strip()}")
    return run


def parts(version):
    return tuple(int(part) for part in version.split("."))


def latest(releases):
    """The published release with the highest vMAJOR.MINOR.PATCH tag; other tags don't count."""
    tagged = [r for r in releases if TAG.fullmatch(r["tag"])]
    return max(tagged, key=lambda r: parts(r["tag"][1:]), default=None)


def parse_commit(subject, body):
    """A commit's part in the next version: its level, and what the change log says of it."""
    match = SUBJECT.fullmatch(subject.strip())
    breaking, lines = [], body.splitlines()
    for n, line in enumerate(lines):
        found = BREAKING.fullmatch(line.rstrip())
        if found:
            text = [found["text"]]
            for more in lines[n + 1:]:
                if not more.strip() or FOOTER.match(more):
                    break
                text.append(more.strip())
            breaking.append(" ".join(t for t in text if t))
    kind = match["type"].lower() if match else None
    if (match and match["bang"]) or breaking:
        level = "major"
    else:
        level = TYPES.get(kind, "none")
    return {"type": kind, "scope": (match["scope"] or "").strip() if match else "", "level": level,
            "description": match["description"].strip() if match else subject.strip(), "breaking": breaking}


def commits_since(tag):
    """The commits since tag, oldest first, merges left out: the commits they merge are counted."""
    if tag:
        if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False).returncode != 0:
            raise Failure(f"the tag {tag} isn't in this checkout: it needs the full history (fetch-depth: 0)")
        if git("merge-base", "--is-ancestor", tag, "HEAD", check=False).returncode != 0:
            raise Failure(f"the latest release, {tag}, isn't in the history of this commit, so the commits since it are unknown")
    log = git("log", "--no-merges", "--reverse", "--format=%s%x1f%b%x1e", f"{tag}..HEAD" if tag else "HEAD").stdout
    commits = []
    for record in log.split("\x1e"):
        fields = record.strip("\n").split("\x1f")
        if len(fields) == 2:
            commits.append(parse_commit(*fields))
    return commits


def level_of(commits):
    return max((c["level"] for c in commits), key=LEVELS.index, default="none")


def bump(version, level):
    major, minor, patch = parts(version)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def page(text):
    """The Workshop page fields of a workshop.txt."""
    fields = {key: [] for key in PAGE_FIELDS}
    for line in build.lines_of(text):
        key, _, value = line.partition("=")
        if key in fields:
            fields[key].append(value)
    return {key: "\n".join(values) for key, values in fields.items()}


def changed_files(tag, paths):
    return [line for line in git("diff", "--name-only", tag, "HEAD", "--", *paths).stdout.splitlines() if line]


def plan(built, since, released):
    """release when the pack or the item's files changed, page when only the Workshop page did, else none."""
    if not since:
        return "release", []
    files = changed_files(since, ["Contents/"])
    if build.content(built.read_text(encoding="utf-8")) != build.content(released.read_text(encoding="utf-8-sig")) or files:
        return "release", files
    before = page(git("show", f"{since}:workshop.txt").stdout)
    if before != page(Path("workshop.txt").read_text(encoding="utf-8-sig")) or changed_files(since, ["preview.png"]):
        return "page", []
    return "none", []


# ---------------------------------------------------------------------------------------------- change log, Markdown

def code(text):
    if text == "":
        return "nothing"
    return f"`{text}`" if "`" not in text and text.strip() == text else build.markdown(text)


def item_link(entry):
    return f"[{build.markdown(entry['title'] or entry['id'])}]({ITEM_URL}{entry['id']})"


def mod_text(entry):
    return code(entry["id"]) + (f" ({item_link({'id': entry['item'], 'title': entry['title']})})" if entry.get("item") else "")


def block(heading, lines):
    """A heading with its list, folded when long."""
    if not lines:
        return None
    body = "\n".join(f"- {line}" for line in lines)
    if len(lines) > FOLD:
        return f"<details><summary>{heading} ({len(lines)})</summary>\n\n{body}\n\n</details>"
    return f"**{heading} ({len(lines)})**\n\n{body}"


def entries(heading, changes, name=code, value=code):
    return [block(f"{heading} added", [f"{name(key)}: {value(new)}" for key, new in changes["added"]]),
            block(f"{heading} changed", [f"{name(key)}: {value(old)} -> {value(new)}" for key, old, new in changes["changed"]]),
            block(f"{heading} removed", [name(key) for key, _ in changes["removed"]])]


def option_name(key):
    page, _, option = key.partition("|")
    return f"{code(page)}: {code(option)}"


def option_value(value):
    return code(value.partition("|")[2])


def label(text):
    return build.markdown(text) if text else "no label"


def in_the_pack(changes, files):
    if changes["first"]:
        counts = [f"{changes['mods']} mods", f"{changes['optional']} optional mods", f"{changes['items']} Workshop items in {changes['collections']} collections",
                  f"{changes['keys']} key binds", f"{changes['options']} mod options", f"{changes['sandbox']} sandbox settings"]
        return "The first release: " + ", ".join(c for c in counts if not c.startswith("0 ")) + "."
    blocks = [block("Mods added", [mod_text(m) for m in changes["mods"]["added"]]),
              block("Mods removed", [mod_text(m) for m in changes["mods"]["removed"]])]
    moved = changes["moved"]
    if moved:
        names = ", ".join(code(m["id"]) for m in moved[:FOLD]) + (f" and {len(moved) - FOLD} more" if len(moved) > FOLD else "")
        blocks.append(f"**Load order:** {len(moved)} {'mod moves' if len(moved) == 1 else 'mods move'}: {names}.")
    blocks += entries("Optional mods", changes["optional"], value=label)
    blocks += entries("Key binds", changes["keys"])
    blocks += entries("Mod options", changes["options"], option_name, option_value)
    blocks += entries("Sandbox settings", changes["sandbox"])
    blocks += entries("Pack settings", changes["settings"])
    blocks += [block("Locks added", [code(l) for l in changes["locks"]["added"]]),
               block("Locks removed", [code(l) for l in changes["locks"]["removed"]]),
               block("Workshop items added", [item_link(i) for i in changes["items"]["added"]]),
               block("Workshop items removed", [item_link(i) for i in changes["items"]["removed"]]),
               block("Sub-collections added", [item_link(c) for c in changes["collections"]["added"]]),
               block("Sub-collections removed", [item_link(c) for c in changes["collections"]["removed"]]),
               block("Files of the Workshop item changed", [code(f) for f in files])]
    blocks = [b for b in blocks if b]
    return "\n\n".join(blocks) or "The pack file's lines changed, with no change to its entries."


def commit_line(commit, text):
    return (f"**{commit['scope']}:** " if commit["scope"] else "") + text


def notes(text, commits, changes, files):
    sections = [text.strip()] if text.strip() else []
    breaking = [commit_line(c, t) for c in commits if c["level"] == "major" for t in (c["breaking"] or [c["description"]])]
    if breaking:
        sections.append("### Breaking changes\n\nExisting worlds may not work with this version: back up your saves first, or start a new world.\n\n"
                        + "\n".join(f"- {line}" for line in breaking))
    for heading, kind in (("Features", "feat"), ("Fixes", "fix")):
        lines = [commit_line(c, c["description"]) for c in commits if c["type"] == kind and c["level"] != "major"]
        if lines:
            sections.append(f"### {heading}\n\n" + "\n".join(f"- {line}" for line in lines))
    sections.append("### In the pack\n\n" + in_the_pack(changes, files))
    return "\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------------------------- change log, Steam

INLINE = [
    (re.compile(r"\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)\)"), r"[url=\2]\1[/url]"),
    (re.compile(r"\*\*(.+?)\*\*"), r"[b]\1[/b]"),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\\([\\`*_\[\]<>#|~!(){}+.-])"), r"\1"),
]


def inline(text):
    for pattern, replacement in INLINE:
        text = pattern.sub(replacement, text)
    return text.replace("&#8203;", "")


def steam_lines(markdown):
    lines, listing = [], False
    for raw in markdown.replace("\r", "").split("\n"):
        line = raw.strip()
        summary = re.fullmatch(r"<details><summary>(.*)</summary>", line)
        item = re.fullmatch(r"[-*] (.*)", line)
        heading = re.fullmatch(r"#{1,6} (.*)", line)
        if listing and not item:
            lines.append("[/list]")
            listing = False
        if line == "</details>" or (line == "" and lines and (lines[-1] == "" or lines[-1].startswith("[b]") and lines[-1].endswith("[/b]"))):
            continue
        if item:
            if not listing:
                lines.append("[list]")
                listing = True
            lines.append("[*]" + inline(item[1]))
        elif summary or heading:
            lines.append(f"[b]{inline((summary or heading)[1])}[/b]")
        else:
            lines.append(inline(line))
    if listing:
        lines.append("[/list]")
    return lines


def steam(markdown, version, url):
    """The change log as Steam shows a change note: BBCode, no double quote (the VDF string is quoted), within Steam's
    limit, else cut with a pointer to the release."""
    def safe(text):
        return text.replace('"', "'").replace("\\", "/")

    lines = [f"[b]Version {version}[/b]", ""] + [safe(line) for line in steam_lines(markdown)]
    while lines and lines[-1] == "":
        lines.pop()
    text = "\n".join(lines)
    if len(text.encode()) <= STEAM_NOTE_MAX:
        return text
    pointer = safe(f"\n\n... The full change log is on GitHub: {url}")
    kept, size, listing = [], 0, False
    for line in lines:
        inside = (listing or line == "[list]") and line != "[/list]"
        if size + len(line.encode()) + 1 + (len("[/list]\n") if inside else 0) + len(pointer.encode()) > STEAM_NOTE_MAX:
            break
        kept.append(line)
        size += len(line.encode()) + 1
        listing = inside
    if listing and kept[-1] == "[list]":
        kept.pop()
    elif listing:
        kept.append("[/list]")
    return "\n".join(kept).rstrip("\n") + pointer


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("latest")
    p.add_argument("releases", type=Path, help="one JSON object per line: tag, prerelease, assets")
    p = sub.add_parser("version")
    p.add_argument("--since")
    p.add_argument("--commits", type=Path, required=True)
    p = sub.add_parser("plan")
    p.add_argument("--built", type=Path, required=True)
    p.add_argument("--since")
    p.add_argument("--released", type=Path)
    p.add_argument("--files", type=Path, required=True)
    p.add_argument("--next", required=True)
    p = sub.add_parser("notes")
    p.add_argument("--notes", default="")
    p.add_argument("--commits", type=Path, required=True)
    p.add_argument("--changes", type=Path, required=True)
    p.add_argument("--files", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("steam")
    p.add_argument("--notes", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "latest":
        releases = [json.loads(line) for line in args.releases.read_text(encoding="utf-8").splitlines() if line.strip()]
        release = latest(releases)
        if release is None:
            print("::notice::There is no release with a vMAJOR.MINOR.PATCH tag yet.")
            return
        if "prepackaged.txt" not in release["assets"]:
            raise Failure(f"the latest release, {release['tag']}, has no prepackaged.txt, so it wasn't made by Publish: "
                          "attach its pack file to it, or delete it")
        pending = "true" if release["prerelease"] else "false"
        print(f"The latest release is {release['tag']}" + (", a pre-release: its Workshop upload hasn't finished." if release["prerelease"] else "."))
        output(tag=release["tag"], version=release["tag"][1:], pending=pending)
    elif args.command == "version":
        if args.since and not TAG.fullmatch(args.since):
            raise Failure(f"--since {args.since} is not vMAJOR.MINOR.PATCH")
        # The first release's change log lists the pack, not the history before it.
        commits = commits_since(args.since) if args.since else []
        args.commits.write_text(json.dumps(commits, indent=1), encoding="utf-8")
        level = level_of(commits) if args.since else "first"
        # A pack that changed gets a patch release at least.
        version = bump(args.since[1:], level) if args.since else "1.0.0"
        print(f"{len(commits)} commits since {args.since or 'the start'}: {level}. A release would be {version}.")
        output(version=version)
    elif args.command == "plan":
        if bool(args.since) != bool(args.released):
            raise Failure("--since and --released go together")
        mode, files = plan(args.built, args.since, args.released)
        args.files.write_text(json.dumps(files), encoding="utf-8")
        if mode == "release":
            print(f"Releasing {args.next}.")
            tag, version = f"v{args.next}", args.next
        elif mode == "page":
            print(f"::notice::The pack is as in {args.since}, but the Workshop page changed: only the page is updated.")
            tag, version = args.since, args.since[1:]
        else:
            print(f"::notice::Nothing to publish: the pack, the item's files and the Workshop page are as in {args.since}.")
            tag, version = args.since, args.since[1:]
        write("GITHUB_ENV", {"MODE": mode, "TAG": tag, "VERSION": version})
    elif args.command == "notes":
        text = notes(args.notes, json.loads(args.commits.read_text(encoding="utf-8")), json.loads(args.changes.read_text(encoding="utf-8")),
                     json.loads(args.files.read_text(encoding="utf-8")))
        args.out.write_text(text, encoding="utf-8")
        print(text)
    elif args.command == "steam":
        args.out.write_text(steam(args.notes.read_text(encoding="utf-8"), args.version, args.url), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except (Failure, build.Failure) as failure:
        print(f"::error::{failure}")
        sys.exit(1)
