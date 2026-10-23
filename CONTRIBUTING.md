# Contributing to Steve's Bitchin' Mod Collection (Prepackaged)

Suggestions for the mod list are welcome as GitHub issues. Using the pack is in the [README](README.md).

## Repository layout

- `pack.txt`: the hand-written part of the pack file.
- `build.py`: writes the pack file from `pack.txt` and the collection ([Changing the pack](#changing-the-pack)).
- `Contents/mods/SBMCPrepackaged/`: the Workshop item. `42/` has `mod.info` and `poster.png`; the pack file goes in `common/`.
- `workshop.txt`: the Workshop page: the item's `id=`, title, description, tags and visibility.
- `preview.png`: the Workshop preview image.
- `publish.py` and `.github/workflows/publish.yml`: the release workflow; `.github/workflows/drift.yml`: a daily check that opens an issue when the collection on Steam no longer matches the latest release.
- `.gitignore`: a whitelist. A new top-level file or folder is ignored until it has its own `!/path` rule.

## Changing the pack

`pack.txt` holds what the collection can't say: the pack's id and name, the top collection, the mods in load order (`Mods`), and later the optional mods, key binds, mod options, sandbox settings and locks. It uses Prepackaged's pack file format, without `version`, `WorkshopItems` and `@collections`, which come from the release and the live collection. A `Mods=` line pasted from a server `.ini` works as well.

```
python3 build.py --version 1.0.0
```

writes the pack file, `Contents/mods/SBMCPrepackaged/common/prepackaged.txt`, from `pack.txt` and the collection as Steam has it now. It needs Python 3 and no secrets. It lists the items whose Mod IDs (the description's `Mod ID:` lines) aren't in `Mods`, and the `Mods` entries no item in the collection declares. Add `--parser <Prepackaged>/Contents/mods/Prepackaged/42/media/lua/shared` to check the file with Prepackaged's own parser, which needs Lua 5.1 (`--lua` names the interpreter).

The pack file is not committed: each release builds it from the collection at that moment.

## Commit messages

Releases are made from the commit messages, so they follow [Conventional Commits](https://www.conventionalcommits.org/). The type decides what the next release is:

| Type | For | Version |
|---|---|---|
| `fix:` | a fix or tweak, such as load order or an option value | patch |
| `feat:` | something players notice: mods added or removed, new defaults | minor |
| `feat!:` (any type with `!`), or a `BREAKING CHANGE:` footer | a change existing worlds may not survive | major |
| `docs:`, `chore:`, `ci:` and others | everything else | none |

A scope is fine (`fix(fog): ...`). Breaking means existing worlds may not survive the update, for example when mods are removed or change how saves work.
