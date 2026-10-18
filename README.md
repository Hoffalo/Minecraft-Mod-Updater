# Minecraft Mod Updater

Checks the jars in your mods folder for updates on Modrinth for the Minecraft version you choose, and swaps them in only once **every** mod is available.

## Setup

Python 3 only, no packages to install. Set your target version once (open a new terminal afterwards so it takes effect):

```bash
setx MC_VERSION 26.4
```

Optional settings:

| Variable | Default | Meaning |
|---|---|---|
| `MC_VERSION` | (required) | Minecraft version to update to |
| `MC_LOADER` | detected from jars | `fabric`, `quilt`, `forge`, `neoforge` |
| `MC_MODS_DIR` | `%APPDATA%\.minecraft\mods` | mods folder |
| `MC_ALLOW_PRERELEASE` | off | `1` = take betas even when a stable release exists |

Each one can also be passed as a flag: `--mc`, `--loader`, `--mods-dir`, `--prerelease`.

## Usage

```bash
python mod_updater.py              # check (the default command)
python mod_updater.py --dry-run    # only show what would change
python mod_updater.py status       # show what's pending, offline
```

Or double-click `check-mods.bat`.

### Window

Double-click `mod-updater-gui.bat` (or run `python mod_updater_gui.py`) for a Minecraft-style window with the same options: version, loader, pre-releases, mods folder, plus **Check for Updates**, **Dry Run** and **Status** buttons and a colored log. Your settings are remembered in `.gui-settings.json`. Install the [Monocraft](https://github.com/IdreesInc/Monocraft) font for the full pixel look.

## What happens on `check`

1. Every `.jar` in the mods folder is identified on Modrinth by its SHA-1 hash.
2. The newest build for `MC_VERSION` + loader is downloaded into a sibling folder, **`mods <version>`** (e.g. `.minecraft\mods 26.4`). Stable releases are preferred over betas.
3. **If every mod has a build:** the staged jars replace the old ones. Old jars go to `mods backup\<timestamp>\`, the staging folder is deleted, and you get a Windows notification.
4. **If some mods are missing:** the mods folder is left alone, the rest stay staged, and the missing ones are listed. Run `check` again whenever you like. It re-checks, picks up anything newly released (or newer), and does step 3 once the last one arrives.

Other details:
- Jars not on Modrinth are listed as `?` and never touched. Update them by hand.
- If you remove a mod from the mods folder while an update is pending, it stops being tracked, so it no longer blocks the swap.
- If Minecraft is running and a jar is locked, the swap is rolled back and nothing changes.
- A warning lists dependencies that the new versions declare but you don't have installed. Some of these are bundled inside the mod already.
