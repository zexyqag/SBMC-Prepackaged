#!/usr/bin/env python3
"""Builds the pack file from pack.txt and the live Steam collection tree, and reports where they differ.

    python3 build.py --version N [--parser DIR] [--compare OLD --issue FILE]
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "pack.txt"
PACK_FILE = "prepackaged.txt"
API = "https://api.steampowered.com/ISteamRemoteStorage/"
ITEM_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id="
BATCH = 100
# EWorkshopFileType
COLLECTION = 2
VERSION = re.compile(r"[1-9][0-9]{0,8}")
GENERATED = {"version": "version", "workshopitems": "WorkshopItems"}

# Descriptions are BBCode, and the game's uploader ends them with a "Mod ID: <id>" line per mod.
BLOCK_TAG = re.compile(r"\[\*\]|\[/?(?:h\d|p|list|olist|li|quote|code|table|tr|td|th|hr|br)\b[^\]]*\]", re.I)
INLINE_TAG = re.compile(r"\[/?[a-z][a-z0-9]*(?:=[^\]]*)?\]", re.I)
LINE_BREAK = re.compile("[\n\v\f\r\x1c-\x1e\x85\u2028\u2029]")
MOD_ID_LINE = re.compile(r"mod ?id\s*:\s*(.+)", re.I)

CHECK = r"""
local dir, file, mod = ...
package.path = dir .. "/?.lua"
local Parse = require "Prepackaged/Parse"
local handle = assert(io.open(file, "rb"))
local result = Parse.read(Parse.lines(handle:read("*a")), mod)
handle:close()
for _, kind in ipairs({ "errors", "warnings" }) do
    for _, note in ipairs(result[kind]) do
        local args = {}
        for i, arg in ipairs(note.args) do
            args[i] = tostring(arg)
        end
        print(kind .. "\t" .. note.line .. "\t" .. note.code .. "\t" .. table.concat(args, " "))
    end
    if result.more[kind] > 0 then
        print(kind .. "\t0\tMore\t" .. result.more[kind])
    end
end
local pack = result.pack
print("counts\t" .. #pack.mods .. "\t" .. #pack.workshopItems .. "\t" .. #pack.collections)
"""


class Failure(Exception):
    pass


def lines_of(text):
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def trim(text):
    return text.strip(" \t")


def split_list(value):
    return [trim(part) for part in value.split(";") if trim(part)]


def read_source(path):
    lines = lines_of(path.read_text(encoding="utf-8-sig"))
    # pack_end: the line after which version and WorkshopItems go, the last key of the first @pack section.
    source = {"lines": lines, "collection": [], "mods": [], "optional": [], "pack_end": None}
    section, packs = None, 0
    for n, raw in enumerate(lines, 1):
        line = trim(raw)
        if line.startswith("@"):
            section = trim(line[1:]).lower()
            if section == "collections":
                raise Failure(f"pack.txt:{n}: @collections is built from the live collection; remove it")
            if section == "pack":
                packs += 1
                if packs == 1:
                    source["pack_end"] = n
            continue
        if line == "" or line.startswith("#"):
            continue
        if section == "pack":
            if packs == 1:
                source["pack_end"] = n
            key, _, value = line.partition("=")
            key = trim(key).lower()
            if key in GENERATED:
                raise Failure(f"pack.txt:{n}: {GENERATED[key]} is built from the release; remove it")
            if key == "collection":
                source["collection"] += split_list(value)
            elif key == "mods":
                source["mods"] += split_list(value.replace("\\", ""))
        elif section == "optional":
            source["optional"].append(trim(line.partition("=")[0].replace("\\", "")))
    if source["pack_end"] is None:
        raise Failure("pack.txt has no @pack section")
    if not source["collection"]:
        raise Failure("pack.txt has no collection = <top collection id>")
    return source


def find_mod():
    infos = sorted((ROOT / "Contents" / "mods").glob("*/*/mod.info"))
    folders = {info.parent.parent for info in infos}
    if len(folders) != 1:
        raise Failure(f"Contents/mods must hold exactly one mod, not {len(folders)}")
    for line in lines_of(infos[0].read_text(encoding="utf-8")):
        if line.startswith("id="):
            return folders.pop(), trim(line[3:])
    raise Failure(f"{infos[0]} has no id=")


def post(method, data):
    request = urllib.request.Request(API + method + "/v1/", data=urllib.parse.urlencode(data).encode())
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)["response"]
        except urllib.error.HTTPError as error:
            if (error.code < 500 and error.code != 429) or attempt == 2:
                raise Failure(f"Steam's {method} answered HTTP {error.code}")
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
            if attempt == 2:
                raise Failure(f"Steam's {method} gave no answer: {error!r}")
        time.sleep(5 * (attempt + 1))


def steam(method, count_key, list_key, ids):
    found = {}
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        data = {count_key: len(chunk)}
        data.update({f"publishedfileids[{i}]": id for i, id in enumerate(chunk)})
        for entry in post(method, data).get(list_key, []):
            found[entry.get("publishedfileid")] = entry
    missing = [id for id in ids if id not in found]
    if missing:
        raise Failure(f"Steam's {method} left out {', '.join(missing)}")
    return found


def walk(tops):
    """Each collection's entries as (is_collection, id), in the collection's own order."""
    collections, queue, seen = {}, list(tops), set(tops)
    while queue:
        details = steam("GetCollectionDetails", "collectioncount", "collectiondetails", queue)
        queue = []
        for id, entry in details.items():
            if entry.get("result") != 1:
                raise Failure(f"Steam can't read collection {id} (result {entry.get('result')}): is it public or unlisted?")
            children = sorted(enumerate(entry.get("children", [])), key=lambda child: (child[1].get("sortorder", 0), child[0]))
            collections[id] = []
            for _, child in children:
                child_id, is_collection = child["publishedfileid"], child.get("filetype") == COLLECTION
                collections[id].append((is_collection, child_id))
                if is_collection and child_id not in seen:
                    seen.add(child_id)
                    queue.append(child_id)
    return collections


def tree_order(tops, collections):
    """Collections and items depth first, each where it first appears."""
    order, items, seen = [], [], set()
    stack = [(True, id) for id in reversed(tops)]
    while stack:
        is_collection, id = stack.pop()
        if id in seen:
            continue
        seen.add(id)
        if is_collection:
            order.append(id)
            stack.extend(reversed(collections[id]))
        else:
            items.append(id)
    return order, items


def plain_lines(description):
    text = INLINE_TAG.sub("", BLOCK_TAG.sub("\n", description))
    return [re.sub(r"[\s\ufeff]+", " ", line).strip(" ") for line in LINE_BREAK.split(text)]


def declared_mod_ids(description):
    ids = []
    for line in plain_lines(description):
        match = MOD_ID_LINE.fullmatch(line)
        if match and not re.search(r"[;\\]", match.group(1)) and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def describe(details):
    """Title and declared mod ids (None when unknown) of each item."""
    info = {}
    for id, entry in details.items():
        ok = entry.get("result") == 1
        title = re.sub(r"[\r\n]+", " ", entry.get("title") or "").strip() if ok else ""
        mods = declared_mod_ids(entry.get("description") or "") if ok else None
        info[id] = {"title": title, "mods": mods or None, "ok": ok, "result": entry.get("result")}
    return info


def build(source, version, collections, order, items, info):
    lines = ["# Built by build.py from pack.txt and the live Steam collection. Edit pack.txt, not this file."]
    for n, line in enumerate(source["lines"], 1):
        lines.append(line)
        if n == source["pack_end"]:
            lines.append(f"version = {version}")
            lines.append("WorkshopItems = " + ";".join(items))
    lines += ["", "@collections"]
    for id in order:
        entries = ";".join(("c:" + child) if is_collection else child for is_collection, child in collections[id])
        lines.append(f"{id} = {info[id]['title']} | {entries}")
    return "\n".join(lines) + "\n"


def check(parser, lua, path, mod_id, expected):
    if not (Path(parser) / "Prepackaged" / "Parse.lua").is_file():
        raise Failure(f"{parser} has no Prepackaged/Parse.lua")
    try:
        run = subprocess.run([lua, "-", str(parser), str(path), mod_id], input=CHECK, capture_output=True, text=True)
    except FileNotFoundError:
        raise Failure(f"{lua} is not installed: install Lua 5.1, or leave out --parser")
    if run.returncode != 0:
        raise Failure(f"Prepackaged's parser failed to run:\n{run.stderr.strip()}")
    errors, counts = 0, None
    for line in run.stdout.splitlines():
        kind, _, rest = line.partition("\t")
        if kind == "counts":
            counts = tuple(int(n) for n in rest.split("\t"))
            continue
        number, code, args = rest.split("\t", 2)
        print(f"{kind[:-1]}: {path.name}:{number}: {code} {args}".rstrip())
        errors += kind == "errors"
    if errors:
        raise Failure(f"Prepackaged's parser found {errors} error(s) in {path.name}")
    if counts != expected:
        raise Failure(f"Prepackaged's parser read (Mods, WorkshopItems, collections) = {counts}, not {expected}")
    print(f"Prepackaged's parser reads {path.name} without errors.")


def loose(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def hints(mod, items, info):
    """Items that may hold a mod no item declares: one that declares the id with other case or punctuation, one that
    declares no id and has the mod's name as its title, or one that declares the start of the id (a sub-mod)."""
    name = loose(mod)
    same, titled, prefix = [], [], []
    for id in items:
        entry = info[id]
        for declared in entry["mods"] or []:
            if loose(declared) == name:
                same.append(f"item {id}, which declares {declared}")
            elif len(loose(declared)) >= 4 and name.startswith(loose(declared)):
                prefix.append(f"item {id}, which declares {declared}")
        if entry["ok"] and entry["mods"] is None and name == loose(re.sub(r"\[[^\]]*\]|\([^)]*\)", "", entry["title"])):
            titled.append(f"item {id} ({entry['title']}), which declares no Mod ID")
    return same or titled or prefix


def mod_report(source, items, info):
    """Items with no mod in the pack, and pack mods no item declares, with hints for those."""
    enabled = set(source["mods"]) | set(source["optional"])
    no_mod = [id for id in items if not (set(info[id]["mods"] or []) & enabled)]
    declared = {mod for id in items for mod in info[id]["mods"] or []}
    no_item = [(mod, hints(mod, items, info)) for mod in dict.fromkeys(source["mods"] + source["optional"]) if mod not in declared]
    return no_mod, no_item


def by_collection(ids, collections, order):
    """ids grouped under the first collection that lists each, in tree order."""
    home = {}
    for collection in order:
        for is_collection, child in collections[collection]:
            if not is_collection:
                home.setdefault(child, collection)
    wanted = set(ids)
    return [(collection, [child for is_collection, child in collections[collection]
                          if not is_collection and child in wanted and home[child] == collection])
            for collection in order]


def mods_text(entry, code=False):
    if entry["mods"] is None:
        return "Mod ID unknown" if entry["ok"] else f"Steam gives no details (result {entry['result']})"
    return "declares " + ", ".join(f"`{mod}`" if code else mod for mod in entry["mods"])


def report(source, collections, order, items, info):
    entries = sum(1 for id in order for is_collection, _ in collections[id] if not is_collection)
    print(f"Tree: {len(order)} collections, {len(items)} items ({entries} item entries).")
    unavailable = [id for id in items if not info[id]["ok"]]
    if unavailable:
        print(f"\nItems Steam gives no details for: {len(unavailable)}")
        for id in unavailable:
            print(f"  {id}  result {info[id]['result']}")
    no_mod, no_item = mod_report(source, items, info)
    unknown = sum(1 for id in no_mod if info[id]["ok"] and info[id]["mods"] is None)
    print(f"\nItems with no Mod ID in Mods or @optional: {len(no_mod)}, of which {unknown} declare none"
          f" and {len(unavailable)} have no details")
    for collection, ids in by_collection(no_mod, collections, order):
        if ids:
            print(f"  {info[collection]['title']} ({collection}):")
        for id in ids:
            print(f"    {id}  {info[id]['title']}  ({mods_text(info[id])})")
    known = sum(1 for id in items if info[id]["mods"] is not None)
    print(f"\nMods and @optional entries no item in the tree declares: {len(no_item)}"
          f" (going by the {known} items that list their Mod IDs; the others may hold some)")
    for mod, found in no_item:
        print(f"  {mod}" + (f"  (maybe in {'; '.join(found)})" if found else ""))


def read_pack(path):
    """WorkshopItems and @collections of a pack file this script built."""
    pack = {"items": [], "collections": {}}
    section = None
    for line in map(trim, lines_of(path.read_text(encoding="utf-8-sig"))):
        if line.startswith("@"):
            section = trim(line[1:]).lower()
        elif line == "" or line.startswith("#"):
            continue
        elif section == "pack":
            key, _, value = line.partition("=")
            if trim(key).lower() == "workshopitems":
                pack["items"] += split_list(value)
        elif section == "collections":
            id, _, rest = line.partition("=")
            title, _, entries = rest.rpartition("|")
            pack["collections"][trim(id)] = {"title": trim(title), "entries": split_list(entries)}
    return pack


def markdown(text):
    return re.sub(r"([\\`*_\[\]<>#|~])", r"\\\1", text).replace("@", "@&#8203;")


def link(id, info, title=""):
    title = info.get(id, {}).get("title") or title or id
    return f"[{markdown(title)}]({ITEM_URL}{id})"


def mod_note(id, info, enabled, old_items):
    if id in old_items:
        return "already in the pack"
    entry = info[id]
    if entry["mods"] is None:
        return mods_text(entry)
    return ", ".join(f"`{mod}`" + ("" if mod in enabled else " (not in Mods)") for mod in entry["mods"])


def drift(old, source, collections, order, items, info):
    """The issue body, or None when the live tree matches the released pack."""
    enabled = set(source["mods"]) | set(source["optional"])
    old_items, live_items = set(old["items"]), set(items)
    new = {id: ["c:" + child if is_collection else child for is_collection, child in collections[id]] for id in order}

    def line(entry, added):
        if entry.startswith("c:"):
            child = entry[2:]
            return f"- collection {link(child, info, old['collections'].get(child, {}).get('title', ''))}"
        if added:
            return f"- {link(entry, info)}: {mod_note(entry, info, enabled, old_items)}"
        return f"- {link(entry, info)}" + (" (still in the collection)" if entry in live_items else "")

    sections = []
    for id in order + [id for id in old["collections"] if id not in new]:
        before, after = old["collections"].get(id), new.get(id)
        blocks = []
        if before is None:
            heading = f"New collection {link(id, info)}"
        elif after is None:
            heading = f"{link(id, info, before['title'])} is no longer in the collection"
        else:
            heading = link(id, info)
            if before["title"] != info[id]["title"]:
                blocks.append(f"Renamed from {markdown(before['title'])}.")
        was, now = (before["entries"] if before else []), (after or [])
        for label, entries, added in (("Added", [e for e in now if e not in was], True),
                                      ("Removed", [e for e in was if e not in now], False)):
            if entries:
                blocks.append(f"{label}:\n\n" + "\n".join(line(entry, added) for entry in entries))
        if blocks or before is None or after is None:
            sections.append("\n\n".join([f"### {heading}"] + blocks))
    if not sections:
        return None
    gained = [id for id in items if id not in old_items]
    lost = [id for id in old["items"] if id not in live_items]
    no_mod, _ = mod_report(source, items, info)
    body = [f"The live collection differs from the pack file of the latest release, {old['tag']}. "
            f"Items new to the pack: {len(gained)}. Items no longer in the collection: {len(lost)}. "
            "Publish a release to update the pack, or change the collection back; this issue closes once they match.",
            *sections]
    if no_mod:
        groups = [f"{link(collection, info)}\n\n" + "\n".join(f"- {link(id, info)}: {mods_text(info[id], code=True)}" for id in ids)
                  for collection, ids in by_collection(no_mod, collections, order) if ids]
        body.append(f"<details><summary>{len(no_mod)} items in the collection have no Mod ID in pack.txt's Mods or "
                    "@optional</summary>\n\n" + "\n\n".join(groups) + "\n\n</details>")
    return "\n\n".join(body) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="the pack's version, a whole number that goes up with every release")
    parser.add_argument("--parser", help="the folder holding Prepackaged/Parse.lua, to check the pack file with it")
    parser.add_argument("--lua", default="lua5.1", help="the Lua 5.1 interpreter (default lua5.1)")
    parser.add_argument("--compare", type=Path, help="a released pack file to compare the live collection with")
    parser.add_argument("--issue", type=Path, help="with --compare: where to write the issue text when they differ")
    args = parser.parse_args()
    if not VERSION.fullmatch(args.version):
        raise Failure(f"--version {args.version} is not a whole number from 1 to 999999999")
    if (args.compare is None) != (args.issue is None):
        raise Failure("--compare and --issue go together")

    source = read_source(SOURCE)
    folder, mod_id = find_mod()
    tops = list(dict.fromkeys(source["collection"]))
    collections = walk(tops)
    order, items = tree_order(tops, collections)
    old = None
    extra = []
    if args.compare:
        old = read_pack(args.compare)
        old["tag"] = f"v{args.version}"
        known = set(order) | set(items)
        extra = [id for id in dict.fromkeys(old["items"] + list(old["collections"])) if id not in known]
    info = describe(steam("GetPublishedFileDetails", "itemcount", "publishedfiledetails", order + items + extra))

    path = folder / "common" / PACK_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(build(source, args.version, collections, order, items, info), encoding="utf-8", newline="\n")
    print(f"Wrote {path.relative_to(ROOT)}: version {args.version}, {len(source['mods'])} Mods, "
          f"{len(items)} WorkshopItems, {len(order)} collections.")
    if args.parser:
        check(args.parser, args.lua, path, mod_id, (len(set(source["mods"])), len(items), len(order)))
    else:
        print("Not checked with Prepackaged's parser: no --parser.")
    print()
    report(source, collections, order, items, info)

    if args.compare:
        body = drift(old, source, collections, order, items, info)
        if body is None:
            args.issue.unlink(missing_ok=True)
            print(f"\nThe live collection matches {args.compare}.")
        else:
            args.issue.write_text(body, encoding="utf-8")
            print(f"\nThe live collection differs from {args.compare}: wrote {args.issue}.")


if __name__ == "__main__":
    try:
        main()
    except Failure as failure:
        print(f"error: {failure}", file=sys.stderr)
        sys.exit(1)
