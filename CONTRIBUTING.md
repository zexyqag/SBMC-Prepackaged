# Contributing to Steve's Bitchin' Mod Collection (Prepackaged)

This is for working on the repository: building the pack file, publishing it and the drift check. Using the pack is in the [README](README.md).

## Repository layout

- `pack.txt`: the hand-written part of the pack file.
- `build.py`: writes the pack file from `pack.txt` and the collection ([Changing the pack](#changing-the-pack)).
- `Contents/mods/SBMCPrepackaged/`: the Workshop item. `42/` has `mod.info` and `poster.png`; the pack file goes in `common/`.
- `workshop.txt`: the Workshop page: the item's `id=`, title, description, tags and visibility.
- `preview.png`: the Workshop preview image.
- `publish.py` and `.github/workflows/publish.yml`: the Publish workflow; `.github/workflows/drift.yml`: the Drift check.
- `.gitignore`: a whitelist. A new top-level file or folder is ignored until it has its own `!/path` rule.

## Not in the repository

- The pack file, `Contents/mods/SBMCPrepackaged/common/prepackaged.txt`: Publish builds it and attaches it to the release.
- The test harness, which lives outside the repository.

## Changing the pack

`pack.txt` holds what the collection can't say: the pack's id and name, the top collection, the mods in load order (`Mods`), and later the optional mods, key binds, mod options, sandbox settings and locks. It uses Prepackaged's pack file format, without `version`, `WorkshopItems` and `@collections`, which come from the release and the live collection. A `Mods=` line pasted from a server `.ini` works as well.

```
python3 build.py --version 1.0.0
```

writes the pack file, `Contents/mods/SBMCPrepackaged/common/prepackaged.txt`, from `pack.txt` and the collection as Steam has it now. It needs Python 3 and no secrets. It lists the items whose Mod IDs (the description's `Mod ID:` lines) aren't in `Mods`, and the `Mods` entries no item in the collection declares. Add `--parser <Prepackaged>/Contents/mods/Prepackaged/42/media/lua/shared` to check the file with Prepackaged's own parser, which needs Lua 5.1 (`--lua` names the interpreter).

The pack file is not committed. Publish builds it from the collection at that moment and attaches it to the GitHub release, so the release holds exactly what was uploaded and no stale copy sits in the repository.

## Publishing

Run the **Publish** workflow (Actions, Publish, Run workflow), optionally with notes for the top of the change log. It finds the latest release, the one with the highest `vMAJOR.MINOR.PATCH` tag (other tags don't count), builds the pack file, checks it with Prepackaged's parser and compares it with that release's `prepackaged.txt`, leaving out the version line, comments and blank lines:

- **Changed**, or a file under `Contents/` changed: a new release. Its version comes from the commits since the latest release, as below, and is at least a patch, also when only the collection changed on Steam. The first release is 1.0.0.
- **Unchanged**, but the title, description or visibility in `workshop.txt`, or `preview.png`, changed: only the Workshop page is updated, and the pack keeps its version.
- **Neither**: nothing is published.

| Commit message | Version |
|---|---|
| `fix: ...` | patch: 1.4.2 to 1.4.3 |
| `feat: ...` | minor: 1.4.2 to 1.5.0 |
| `feat!: ...` (any type with `!`), or a `BREAKING CHANGE:` footer | major: 1.4.2 to 2.0.0 |
| any other type (`docs:`, `chore:`, ...) or message | none |

A scope is fine (`fix(maps): ...`), and the highest of the commits counts. Breaking means existing worlds may not survive the update, for example when mods are removed or change how saves work: start a new world or back up first. Prepackaged warns players about a new major version and lists the mods an update disables.

A release is tagged `vX.Y.Z` at the commit the run built and named after its version. Its notes are the change log: the run's notes, the breaking changes, features and fixes from the commit messages, then what changed in the pack itself (mods added and removed with the Workshop item that declares each, load order moves, optional mods, key binds, mod options and sandbox settings, Workshop items and sub-collections), with long lists folded. `prepackaged.txt` is attached, and `Contents/` and `preview.png` are uploaded with the title, description and visibility in `workshop.txt` and the change log as the Steam change note.

When the upload fails, the release stays a pre-release and the run fails. Run Publish again: it retries the upload of that release, as it was built, before it publishes anything newer. To give it up instead, delete the release and its tag.

Setup: the `steam` environment holds the secrets `STEAM_USERNAME` and `STEAM_CONFIG_VDF` (steamcmd's `config/config.vdf` after a login with Steam Guard), and the repository variable `PREPACKAGED_REPO` names Prepackaged's GitHub repository. While `workshop.txt` has no `id=`, a release is only built and its `prepackaged.txt` attached, because the item doesn't exist yet. Put that file in `Contents/mods/SBMCPrepackaged/common/`, upload the item from the game, which creates it, sets its tags and writes its `id=` into `workshop.txt`, and commit that. Later releases upload by themselves.

## Drift check

Every day the Drift workflow builds the lists from the live collection and compares them with the latest release's `prepackaged.txt`. While they differ, one issue lists the items added and removed in each sub-collection and the items with no Mod ID in `Mods`. It closes once they match again, usually after the next Publish. GitHub pauses the schedule after 60 days without a commit; the Actions tab turns it back on.
