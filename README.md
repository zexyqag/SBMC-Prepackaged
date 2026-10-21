# Steve's Bitchin' Mod Collection (Prepackaged)

The Prepackaged pack for [Steve's Bitchin' Mod Collection](https://steamcommunity.com/sharedfiles/filedetails/?id=3556493168), a Project Zomboid Build 42 Workshop collection. This repository is the pack's Workshop item folder.

## Using the pack

1. Subscribe to the collection, this pack and Prepackaged, and enable Prepackaged in the Mods screen.
2. In the main menu, press **Prepackaged**, pick Steve's Bitchin' Mod Collection, press **Use this pack** and check the review.
3. **Apply** backs up your mod list and puts the pack's mods in place, in the pack's load order.

## Changing the pack

`pack.txt` holds what the collection can't say: the pack's id and name, the top collection, the mods in load order (`Mods`), and later the optional mods, key binds, mod options, sandbox settings and locks. It uses Prepackaged's pack file format, without `version`, `WorkshopItems` and `@collections`, which come from the release and the live collection. A `Mods=` line pasted from a server `.ini` works as well.

```
python3 build.py --version 1
```

writes the pack file, `Contents/mods/SBMCPrepackaged/common/prepackaged.txt`, from `pack.txt` and the collection as Steam has it now. It needs Python 3 and no secrets. It lists the items whose Mod IDs (the description's `Mod ID:` lines) aren't in `Mods`, and the `Mods` entries no item in the collection declares. Add `--parser <Prepackaged>/Contents/mods/Prepackaged/42/media/lua/shared` to check the file with Prepackaged's own parser, which needs Lua 5.1 (`--lua` names the interpreter).

The pack file is not committed. Each release builds it from the collection at that moment and attaches it to the GitHub release, so the release holds exactly what was uploaded and no stale copy sits in the repository.

## Releases

Publish a GitHub release tagged `v<N>`, with N a whole number above the last release's: it becomes the pack's `version`, and players are offered the update. The Workshop workflow then:

1. builds the pack file and checks it with Prepackaged's parser;
2. uploads `Contents/` and `preview.png`, with the title, description and visibility in `workshop.txt` and the release notes as the change note;
3. attaches `prepackaged.txt` to the release.

Running the workflow by hand with a release's tag uploads that release again.

Setup: the `steam` environment holds the secrets `STEAM_USERNAME` and `STEAM_CONFIG_VDF` (steamcmd's `config/config.vdf` after a login with Steam Guard), and the repository variable `PREPACKAGED_REPO` names Prepackaged's GitHub repository. While `workshop.txt` has no `id=`, a release is only built and its `prepackaged.txt` attached, because the item doesn't exist yet. Put that file in `Contents/mods/SBMCPrepackaged/common/`, upload the item from the game, which creates it, sets its tags and writes its `id=` into `workshop.txt`, and commit that. Later releases upload by themselves.

## Drift check

Every day the Drift workflow builds the lists from the live collection and compares them with the latest release's `prepackaged.txt`. While they differ, one issue lists the items added and removed in each sub-collection and the items with no Mod ID in `Mods`. It closes once they match again, usually after the next release. GitHub pauses the schedule after 60 days without a commit; the Actions tab turns it back on.
