# Testing on a real Steam install

The automated tests use a fake Steam folder. Before a release, check these on
a real desktop and on a Steam Deck in Desktop Mode. Use a game you can test
quickly.

Before you start, make a safety copy of Steam's two files (autoproton backs
them up too, but this is independent of it):

```bash
cp ~/.steam/steam/config/config.vdf ~/config.vdf.manual-backup
cp ~/.steam/steam/userdata/*/config/localconfig.vdf ~/localconfig.vdf.manual-backup
```

## 1. Steam's internal names

- [ ] In Steam, open a game's Properties → Compatibility, force **Proton 9.0**, exit Steam.
      `grep -A3 '"<appid>"' ~/.steam/steam/config/config.vdf` shows `"name" "proton_9"`.
- [ ] Repeat with the newest official Proton (expect `proton_10`) and **Proton Experimental**
      (expect `proton_experimental`). If a name differs, update `steam/compat.py`.

## 2. Applying

- [ ] Start the AppImage. Games are listed with ProtonDB tiers, and the device in the status bar is right.
- [ ] Pick a game with no launch options. Leave the defaults, then **Review and apply…** → **Apply**.
      With Steam running, **Close Steam for me** closes it within a few seconds.
- [ ] **Start Steam**. The game's Properties shows the launch options exactly as previewed, and the forced Proton version.
- [ ] The game starts and runs.
- [ ] Compare `localconfig.vdf` with the newest backup
      (`zcat ~/.local/share/autoproton/backups/<newest>/localconfig.vdf.gz | diff - <(cat …/localconfig.vdf)`).
      Only the `LaunchOptions` line differs.

## 3. Existing launch options and quoting

- [ ] Give a game launch options with quotes in Steam, e.g. `WINEDLLOVERRIDES="dinput8=n,b" %command%`. Exit Steam.
- [ ] In autoproton, tick an extra option with **Add to my current launch options**, then apply.
      Steam shows your original text unchanged plus the new option, quotes intact.

## 4. Undo

- [ ] **History and undo…** → **Undo this change** → apply. In Steam, the compatibility setting and
      launch options are back to what they were.
- [ ] Restart Steam twice. The settings stay (Steam Cloud doesn't bring the other ones back).
- [ ] Change a setting in Steam yourself, then undo an older autoproton change. autoproton warns that
      the settings changed since.

## 5. GE-Proton

- [ ] With no GE-Proton installed, choose GE-Proton for a game. The review offers the download.
      The progress bar moves, and the install ends up in `~/.steam/steam/compatibilitytools.d/GE-Proton…`.
- [ ] After starting Steam, the game's Compatibility setting shows the GE-Proton version and the game runs on it.
- [ ] Cancel a download halfway. No `.autoproton-*` files are left in `compatibilitytools.d`.

## 6. Official Proton that isn't installed

- [ ] Force an official Proton you don't have (an older one). Start the game.
      Steam downloads that Proton first. If it doesn't, note it, because the README says it does.

## 7. Steam variants

- [ ] Flatpak Steam (`flatpak install com.valvesoftware.Steam`): it's found, closing and starting Steam work,
      and GE-Proton lands in the Flatpak's `compatibilitytools.d`.
- [ ] Two Steam accounts: the account picker changes which `userdata/<id>` is edited.
- [ ] Steam Deck, Desktop Mode: the device shows as Steam Deck, and recommendations say "Steam Deck reports".
- [ ] Steam Deck, Game Mode (if launched as a non-Steam game): saving says to switch to Desktop Mode
      and doesn't offer to close Steam.

## 8. Native Linux game

- [ ] Pick a native game you've forced Proton on. The recommendation is "stop forcing Proton".
      After applying, the game runs natively.

## 9. Recovery

- [ ] `QT_QPA_PLATFORM=bogus ./autoproton-*.AppImage` fails to open a window. Then
      `./autoproton-*.AppImage --cli revert <appid>` still works from the terminal.
- [ ] `./autoproton-*.AppImage --cli restore-file <backup id> --file localconfig` puts the whole file back
      (the file as it was just before is backed up first).

## 10. Desktops

- [ ] KDE Plasma (Wayland), GNOME (Wayland), and an X11 session: the window opens and dialogs work.
- [ ] Links (Open on ProtonDB, About) open in the browser, and the browser works normally.
      Bundled libraries must not leak into it.
