# autoproton — Steam + ProtonDB auto-settings (desktop app)

## Context
The user wants a desktop app that picks the best settings for a Steam game from
ProtonDB reports (which Proton version to use, plus launch options) and writes
them into Steam. Today that means reading dozens of reports by hand and
copy-pasting launch options. Repo `gamesensicals/autoproton` is empty; work on
branch `claude/nifty-franklin-jr2021`.

User decisions:
- **Desktop GUI** app, shipped as an **AppImage**: one file that runs on any Linux distro, including SteamOS.
- If reports favour **GE-Proton** and it isn't installed: offer to download it, **ask first**.
- **Preview, then confirm** before writing. **Back up previous settings** so a
  user can always revert to a known-working configuration.

## Verified during planning (live)
- `GET https://www.protondb.com/api/v1/reports/summaries/{appid}.json` → tier, score, confidence, total, trendingTier.
- `GET https://www.protondb.com/data/counts.json` → includes `reports` and `timestamp`.
- **Every report for one game:** `GET https://www.protondb.com/data/reports/all-devices/app/{key}.json`.
  `key` comes from a hash in ProtonDB's site JS. I rebuilt it in Python and
  confirmed it: Cyberpunk (1091500) returned all 2,740 reports.
  ```
  R(e,t,n) = f"{t}p{jsnum(float(e) * (float(t) % float(n)))}"   # NaN when e == "all"
  I(s)     = abs(java_hash32(s + "m"))                            # h = (h<<5) - h + ord(c), int32
  key      = I("p" + R(appid, reports, ts) + "*vRT" + R("all", appid, ts) + "undefined")
  ```
  The key changes whenever `counts.json` changes, so fetch counts first on every lookup.
- **Report fields used:** `timestamp`, `device.hardwareType` (`pc`/`steamDeck`), `device.inferred.steam.gpu`,
  `responses.verdict`, `verdictOob`, `triedOob`, `variant` (`official`/`ge`/`experimental`/`native`/`notListed`/`older`),
  `protonVersion` (`10.0-3`), `customProtonVersion` (`GE-Proton10-34`), `launchOptions`, `*Faults`, `notes`.
- **What the data looks like** (6 popular games, working reports from the last 12 months):
  - Proton version: official Proton 10 wins for most games, GE-Proton 10 comes second. Dota 2 is mostly `native`.
  - Launch options: game-specific fixes are common, e.g. Cyberpunk `--launcher-skip` (41%) and Dota `-sdlaudiodriver pulse` (29%).
    Personal-taste extras (`gamemoderun`, `mangohud`, HDR/Wayland vars) sit around 8–17%. Typos and one-offs
    (`&command%`, `PROTON_UNE_NTSYNC`) appear once each.
- ProtonDB data is ODbL-licensed (bdefore/protondb-data). The per-game endpoint is unofficial, so the app caches results and identifies itself in its User-Agent.

## Stack
Python 3.12, **PySide6-Essentials** (Qt). That's the PySide6 build without
WebEngine and other heavy modules, so the AppImage stays small. Qt looks
native on KDE and Steam Deck desktop mode. GTK/PyGObject was ruled out because
it needs system libraries that are awkward to bundle. Other dependencies are
`vdf` for Steam's text VDF files and `httpx`. `pyproject.toml` (setuptools)
also works as a normal pip install for development.

## Shipping as an AppImage
One file, `autoproton-<version>-x86_64.AppImage`: download, `chmod +x`, double-click.
It runs on any Linux distro, including SteamOS on the Steam Deck. Windows and macOS
are not targets because Proton only exists on Linux.
- **Build**: PyInstaller (one-folder mode) → an `AppDir` with `AppRun`, a `.desktop` file and an icon → `appimagetool`.
  `appimagetool` uses the static type-2 runtime, so users don't need to install `libfuse2`.
  Script: `packaging/build_appimage.sh`, plus `packaging/autoproton.spec`, `packaging/AppRun`,
  `packaging/autoproton.desktop` and `packaging/autoproton.svg`.
- **Release builds run in GitHub Actions** (`.github/workflows/appimage.yml`) on `ubuntu-22.04`. The AppImage needs at least
  the glibc it was built with: 2.35 there covers Ubuntu 22.04+, Debian 12+, Fedora 36+, Arch and SteamOS. The sandbox has
  2.39, so builds made here are only for smoke tests. The workflow runs the tests, builds, uploads the AppImage as a
  build artifact, and attaches it to a GitHub Release when a `v*` tag is pushed.
- **One binary, two modes**: `autoproton` opens the GUI. `autoproton --cli history <appid>` / `revert …` / `backups` /
  `restore-file …` is the no-GUI fallback for reverting from a terminal if the window won't open.
  `autoproton --self-test` checks the bundled Qt, the network and the Steam paths, for bug reports and CI.
- **Child processes get a clean environment**: PyInstaller and AppImage set `LD_LIBRARY_PATH` and Qt/Python variables.
  If those reach `steam` when the app closes or starts it, Steam can break. `process.py` restores the originals
  (`*_ORIG` values, or removes them) before every subprocess, and before `xdg-open` for links.
- **Qt on bare systems**: bundle `libxcb-cursor` and the xcb/wayland platform plugins. Qt picks Wayland or X11 automatically.
  `AppRun` sets nothing distro-specific.
- Data lives outside the read-only AppImage (`~/.local/share/autoproton`, `~/.cache/autoproton`), so updating
  to a new AppImage keeps backups and history.

## Layout
```
autoproton/
  pyproject.toml  README.md  .gitignore  LICENSE (MIT)
  autoproton/
    __init__.py
    protondb.py      # counts → key hash → fetch summary + reports; disk cache (~/.cache/autoproton), keyed by appid+counts timestamp, 24 h TTL
    recommend.py     # PURE functions: reports + HardwareProfile + installed tools → Recommendation
    launchopts.py    # PURE: parse a launch-options string → env / wrappers / %command% / args; assemble; merge with existing
    hardware.py      # Steam Deck (DMI board_vendor=Valve), GPU vendor (/sys/class/drm/*/device/vendor), installed wrappers (shutil.which)
    steam/
      paths.py       # find Steam root: ~/.steam/steam, ~/.local/share/Steam, Flatpak ~/.var/app/com.valvesoftware.Steam/...
      library.py     # libraryfolders.vdf + appmanifest_*.acf → installed games (appid, name)
      users.py       # loginusers.vdf → most-recent account → userdata/<accountid>
      config.py      # read/write LaunchOptions (localconfig.vdf) and CompatToolMapping (config.vdf); case-insensitive keys
      tools.py       # installed compat tools: official Protons (appmanifests + compat_log.txt names), compatibilitytools.d/*/compatibilitytool.vdf
      process.py     # is Steam running (steam.pid + /proc), close it (`steam -shutdown` / flatpak run … -shutdown), wait, relaunch
    geproton.py      # latest GE-Proton release from GitHub API → download tar.gz + .sha512sum → verify → extract to compatibilitytools.d (staging dir, then rename)
    backup.py        # whole-file backups + per-game settings history; restore
    vdfio.py         # load/dump with the vdf package; atomic write (temp file, fsync, rename); re-parse after write to verify
    cli.py           # `autoproton --cli …`: games | history <appid> | revert <appid> [--to N] | backups | restore-file <id>
    __main__.py      # entry: GUI by default, --cli / --self-test switches
    gui/
      app.py         # QApplication entry, main window
      games_view.py  # installed games list (name, ProtonDB tier badge, current tool), search box
      detail_view.py # current vs recommended, toggles, evidence, Apply
      history_view.py# per-game history with Revert; "restore whole file" dialog
      workers.py     # QThreadPool/QRunnable wrappers so every network/disk call stays off the UI thread
  packaging/  build_appimage.sh  autoproton.spec  AppRun  autoproton.desktop  autoproton.svg
  .github/workflows/appimage.yml
  tests/  (see Verification)
```

## Recommender (`recommend.py`, pure and unit-tested)
1. **Pick reports**: keep `verdict == "yes"` reports from the last 24 months.
   Match the user's device (Steam Deck vs PC). If fewer than 10 match, use all devices.
2. **Weight** each report: recency half-life 180 days × (1 − 0.1 × number of `*Faults == "yes"`, floor 0.3)
   × 1.2 when the GPU vendor matches the user's.
3. **Proton version**: group by `native` / `proton_experimental` / official major (`10.0-3` → `proton_10`) /
   GE major (`GE-Proton10-34` → GE 10). Skip `notListed` and `older`. Highest weighted score wins.
   - Native wins → recommend clearing any forced tool.
   - GE wins → newest installed GE-Proton at or above that major. If none is installed, the recommendation
     says "download latest GE-Proton" (asked in the Apply flow).
   - The preview shows the runner-up and each option's report count.
4. **Launch options** (`launchopts.py`): split each string with shlex into env vars (`KEY=VAL` before `%command%`),
   wrappers (other words before `%command%`), and game args (after it; or the whole string when there's no `%command%`).
   A flag followed by a non-flag word is one item (`-sdlaudiodriver pulse`). Count each item, by key, once per report.
   - **Ticked by default** if it appears in ≥ 15% of the weighted working reports **and** in at least 3 reports.
     Reports whose `verdictOob == "no"` count double, since there the option was needed to get the game working.
   - **Shown but unticked:** personal-taste items. These are wrappers (`gamemoderun`, `mangohud`, `gamescope`,
     `game-performance`) and display vars (`PROTON_ENABLE_WAYLAND`, `PROTON_ENABLE_HDR`, `DXVK_HDR`, `ENABLE_HDR_WSI`, `MANGOHUD`).
     Also unticked: resolution args like `-width 1920`, and anything between 5% and 15%.
   - **Hidden:** wrappers that aren't installed; vendor-specific vars for the wrong GPU (`PROTON_ENABLE_NVAPI`, `PROTON_DLSS_UPGRADE` → NVIDIA;
     `PROTON_FSR4_UPGRADE` → AMD); anything in fewer than 3 reports or under 5%.
   - For a key with several values (`WINEDLLOVERRIDES`), use the most-voted value.
   - Assemble as `ENV… wrappers %command% args`. Merge with the user's existing options by default (keep theirs, add ticked items,
     recommended value wins on a key clash); "replace" is a toggle.
5. **Evidence** for the preview: tier, report counts, the share of reports behind each item, and 3 recent verdict notes.

## Apply flow (GUI)
1. Select a game → background fetch → detail view shows **Current** vs **Recommended** (Proton version + launch options with checkboxes).
2. **Apply** → confirmation dialog with the exact before/after text.
3. If GE-Proton is needed and missing → "Download GE-Proton X (~500 MB)?" → progress bar, checksum check, install.
4. If Steam is running → "Steam must be closed to save settings. Close Steam now?" → `-shutdown`, wait up to 30 s for the process to exit.
5. Back up, write both files atomically, re-read them to verify, add a history entry → "Applied. Start Steam?" button.
   Applying while Steam runs is never allowed, because Steam overwrites these files when it exits.

## Backups and revert (`backup.py`, under `~/.local/share/autoproton/`)
- **Whole-file backups**: before every write, copy `localconfig.vdf` and `config.vdf` to `backups/<timestamp>/`
  along with a `manifest.json` (source paths, sha256, the game it was for). Keep the last 30 sets.
- **Per-game history** (`history/<appid>.json`): one entry per apply or revert with timestamp, source
  (`autoproton`/`revert`), previous and new `{launch_options, compat_tool}`. A missing value means "no entry",
  so reverting deletes the key instead of writing an empty string.
- Applying when Steam's current settings differ from the last recorded state records the current settings first
  as "found in Steam". That way the user's own hand-made setup can always be got back.
- **GUI**: a History tab per game lists entries ("Mar 3 — GE-Proton10-34, `--launcher-skip`"). Each has
  **Revert to this**, which uses the same safe write path (back up → Steam-closed check → write → verify).
  Menu → "Restore full Steam config backup…" for disaster recovery.
- **CLI fallback** (same AppImage): `autoproton --cli history <appid>`, `revert <appid> [--to N]`, `backups`, `restore-file <id>`.
- A write that fails verification restores the backup immediately and shows the error.

## Edge cases handled
Flatpak Steam; multiple Steam accounts (default most-recent, account picker in settings); several library folders;
non-Steam shortcuts (not listed); games with no reports (says so, Apply disabled);
ProtonDB key algorithm changing (a 404 shows a clear "ProtonDB changed its data format" error, and the summary tier still shows);
unreadable or read-only files (clear error, nothing written); official Proton internal names come from `compat_log.txt`
when present, else a built-in table (`proton_experimental`, `proton_hotfix`, `proton_10`, `proton_9`, `proton_8`, `proton_7`, `proton_63`, …);
recommended official Proton not installed (offer `steam://install/<toolappid>` after Steam restarts, or keep the current one).

## Verification
- **Unit tests (pytest)**: key hash against the known pair (1091500 + saved counts → known key); recommender on saved real
  report fixtures (Cyberpunk → proton_10 + `--launcher-skip` ticked, `gamemoderun` unticked; Dota 2 → native +
  `-sdlaudiodriver pulse`); launch-option parsing (no `%command%`, quotes, typos, merge/replace).
- **Steam I/O tests** on a fake Steam tree in `tmp_path` (libraryfolders, appmanifests, loginusers, localconfig, config,
  compatibilitytools.d): apply → files changed, backup + history written; revert → exact original bytes come back;
  a "no prior entry" revert deletes the key; a failed verify triggers rollback; refuses while a fake steam.pid process is alive.
- **GE download test** with a mocked GitHub API and a small fake tarball + sha512 (good and bad checksum).
- **GUI tests**: pytest-qt with `QT_QPA_PLATFORM=offscreen` drive load → select → toggle → apply → history → revert against the fake
  Steam tree, with the network mocked. Screenshots of the main window go to the scratchpad for a visual check.
- **Live check in the sandbox**: run the ProtonDB client against real endpoints for 3 games.
- **AppImage smoke test in the sandbox**: build with `packaging/build_appimage.sh`, then run
  `./autoproton-*.AppImage --appimage-extract-and-run --self-test` with `QT_QPA_PLATFORM=offscreen`. The sandbox has no FUSE,
  hence `--appimage-extract-and-run`. Open the main window against the fake Steam tree and screenshot it. Also confirm
  a child process started by the app sees no bundled `LD_LIBRARY_PATH`.
- **CI**: the workflow builds on `ubuntu-22.04` and runs the same `--self-test` on the built AppImage. Its result is what
  gets handed to you.
- **Manual checklist for your machine** (no Steam here): download the AppImage from the workflow run, `chmod +x`, launch it,
  apply to one game, start Steam → check Properties →
  Launch Options and Compatibility show the new values → revert → check the old values are back.

## Delivery
First commit: this plan as `docs/PLAN.md` in the repo, so it can be opened in the app (the plan file itself sits in a
system folder the app can't open). Then commit in logical steps on `claude/nifty-franklin-jr2021` and push to
`gamesensicals/autoproton`. README covers downloading and running the AppImage, a developer install
(venv/pipx), usage, how backups work, a ProtonDB/ODbL attribution, and the manual test checklist. No PR unless you ask.

## Refinements adopted from the design review
- **No `vdf` package for writing.** `vdf.dumps` escapes every `'` and `?`, so a full rewrite would alter
  unrelated parts of `localconfig.vdf`. autoproton has its own small text-VDF parser that edits only the
  bytes being changed (`steam/vdftext.py`). Everything else in the file stays byte-for-byte identical.
- **stdlib `urllib`** instead of `httpx` (one dependency fewer). The core never imports Qt, so the CLI works without it.
- **Launch options from reports are untrusted.** Steam runs them through a shell, so components from reports are
  filtered: wrappers only from an allowlist (and only if installed), no shell metacharacters, no `LD_PRELOAD`/`PATH`-style
  variables, no file paths. The user's own existing options are kept as typed (merge inserts text around `%command%`).
- **Scoring** uses the Wilson lower bound of the weighted success rate, so 3 glowing reports don't beat 300 good ones.
  Ties within 0.03 prefer the current tool, then official Proton, then Experimental, then GE (avoids needless downloads).
- **Revert is field-level.** It restores only this game's `LaunchOptions` and compat mapping, because Steam rewrites
  `localconfig.vdf` constantly (playtime etc.) and a whole-file restore would lose that. Whole-file restore exists for
  disaster recovery and itself takes a backup first.
- **Crash safety**: a lock file stops two copies writing at once, a `pending.json` journal is written before the files,
  and leftover journals are checked at the next start.
- **Steam Deck Game Mode**: the app won't close Steam there; it asks you to use Desktop Mode.
- **GE-Proton tarballs** are extracted with a safety filter (no `..`, no absolute links, no device files) into a
  staging folder, checked for `compatibilitytool.vdf`, then renamed into place.
