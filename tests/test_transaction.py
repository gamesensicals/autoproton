import json
import os

import pytest
from fakesteam import make_proc

from autoproton import atomic, transaction
from autoproton.history import History
from autoproton.models import CompatSetting, GameSettings
from autoproton.steam import locate
from autoproton.transaction import ChangePlan, Store


def plan_for(ctx, appid=1091500, after=None, before=None):
    inst = locate.find_installs(ctx)[0]
    acct = locate.accounts(inst)[0]
    before = before or transaction.current_settings(inst, acct, appid)
    after = after or GameSettings("%command% --launcher-skip", CompatSetting("GE-Proton10-25"))
    return ChangePlan(inst, acct, appid, "Cyberpunk 2077", before, after)


def undo(ctx, store, entry):
    """Undo ``entry`` the way the app does (core.revert_plan)."""
    plan = plan_for(ctx, appid=entry.appid, after=entry.before)
    return transaction.execute(ctx, store, ChangePlan(**{**plan.__dict__, "action": "revert", "ref": entry.id}))


def test_apply_writes_backs_up_and_logs(ctx, steam):
    store = Store(ctx)
    orig_local, orig_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    plan = plan_for(ctx)
    entry = transaction.execute(ctx, store, plan)

    assert transaction.current_settings(plan.install, plan.account, 1091500) == plan.after
    backup = store.backups.get(entry.backup_set)
    assert store.backups.read(backup, "localconfig") == orig_local
    assert store.backups.read(backup, "config") == orig_cfg
    (logged,) = store.history.entries(appid=1091500)
    assert logged.before.launch_options == 'WINEDLLOVERRIDES="winmm,version=n,b" %command%'
    assert logged.after == plan.after
    assert not store.journal_path.exists()
    # the symlinked ~/.steam/steam path still points at the real root
    assert os.path.islink(ctx.home / ".steam/steam")


def test_revert_restores_exact_original_bytes(ctx, steam):
    store = Store(ctx)
    orig_local, orig_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    entry = transaction.execute(ctx, store, plan_for(ctx))
    undo(ctx, store, entry)
    assert steam.localconfig.read_bytes() == orig_local
    assert steam.config.read_bytes() == orig_cfg
    assert [e.action for e in store.history.entries()] == ["revert", "apply"]


def test_revert_of_game_without_prior_settings_removes_keys(ctx, steam):
    store = Store(ctx)
    orig_local, orig_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings("-novid", CompatSetting("proton_10"))))
    assert entry.before == GameSettings(None, None)
    undo(ctx, store, entry)
    assert steam.localconfig.read_bytes() == orig_local
    assert steam.config.read_bytes() == orig_cfg


def test_refuses_while_steam_runs(ctx, steam):
    make_proc(ctx.sys_root, 777, "steam")
    before = steam.localconfig.read_bytes()
    with pytest.raises(transaction.SteamRunning):
        transaction.execute(ctx, Store(ctx), plan_for(ctx))
    assert steam.localconfig.read_bytes() == before


def test_stale_state_detected(ctx, steam):
    plan = plan_for(ctx)
    text = steam.localconfig.read_text().replace("winmm,version=n,b", "dxgi=n")
    steam.localconfig.write_text(text)
    with pytest.raises(transaction.StaleState) as err:
        transaction.execute(ctx, Store(ctx), plan)
    assert "dxgi" in err.value.current.launch_options


def test_nothing_to_do(ctx, steam):
    plan = plan_for(ctx)
    with pytest.raises(transaction.NothingToDo):
        transaction.execute(ctx, Store(ctx), ChangePlan(**{**plan.__dict__, "after": plan.before}))


def test_failure_on_second_file_rolls_back_first(ctx, steam, monkeypatch):
    store = Store(ctx)
    orig_local, orig_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    real_write = atomic.atomic_write
    calls = []

    def flaky(path, data, *a, **k):
        calls.append(path)
        if str(path).endswith("config.vdf") and len(calls) == 2:
            raise OSError("disk full")
        return real_write(path, data, *a, **k)

    monkeypatch.setattr(transaction, "atomic_write", flaky)
    with pytest.raises(OSError):
        transaction.execute(ctx, store, plan_for(ctx))
    assert steam.localconfig.read_bytes() == orig_local
    assert steam.config.read_bytes() == orig_cfg
    assert store.history.entries() == []
    assert not store.journal_path.exists()


def test_failed_verification_rolls_back(ctx, steam, monkeypatch):
    store = Store(ctx)
    orig_local = steam.localconfig.read_bytes()
    plan = plan_for(ctx)
    real = transaction.current_settings
    seen = []

    def lying(*a):
        seen.append(1)
        return GameSettings("tampered", None)

    monkeypatch.setattr(transaction, "current_settings", lying)
    with pytest.raises(transaction.VerifyFailed):
        transaction.execute(ctx, store, plan)
    monkeypatch.setattr(transaction, "current_settings", real)
    assert seen and steam.localconfig.read_bytes() == orig_local


def test_concurrent_lock(ctx, steam):
    store = Store(ctx)
    with store.lock():
        with pytest.raises(transaction.Busy):
            transaction.execute(ctx, store, plan_for(ctx))


def _fake_journal(ctx, store, steam, state):
    """Simulate a crash after the journal was written."""
    plan = plan_for(ctx)
    old = steam.localconfig.read_bytes()
    from autoproton.steam import config_store
    new_local, _ = config_store.build_changes(old, steam.config.read_bytes(), 1091500,
                                              GameSettings("%command% -x", None))
    backup = store.backups.create([("localconfig", steam.localconfig, old)], reason="apply")
    entry = {"type": "change", "id": "e1", "ts": 1.0, "action": "apply", "steam_root": str(plan.install.root),
             "account_id": plan.account.account_id, "appid": 1091500, "game": "Cyberpunk 2077",
             "before": plan.before.to_json(), "after": {"launch_options": "%command% -x", "compat_tool": None},
             "backup_set": backup.id}
    files = [{"path": str(steam.localconfig.resolve()), "old": atomic.sha256(old), "new": atomic.sha256(new_local)}]
    store.dir.mkdir(parents=True, exist_ok=True)
    store.journal_path.write_text(json.dumps({"entry": entry, "files": files}))
    if state == "new":
        steam.localconfig.write_bytes(new_local)
    elif state == "other":
        steam.localconfig.write_bytes(new_local)
        files.append({"path": str(steam.config.resolve()), "old": "x", "new": atomic.file_sha256(steam.config)})
        files[0], files[1] = files[0], files[1]
        store.journal_path.write_text(json.dumps({"entry": entry, "files": [files[0], {**files[1], "new": "y"}]}))
    return old, new_local


def test_recovery_finishes_completed_change(ctx, steam):
    store = Store(ctx)
    _fake_journal(ctx, store, steam, "new")
    with pytest.raises(transaction.RecoveryNeeded):
        transaction.execute(ctx, store, plan_for(ctx, before=GameSettings("%command% -x", None)))
    msg = transaction.recover(ctx, store)
    assert "Finished" in msg
    assert [e.id for e in store.history.entries()] == ["e1"]
    assert transaction.recover(ctx, store) is None


def test_recovery_discards_unstarted_change(ctx, steam):
    store = Store(ctx)
    old, _ = _fake_journal(ctx, store, steam, "old")
    assert "nothing was changed" in transaction.recover(ctx, store)
    assert steam.localconfig.read_bytes() == old and store.history.entries() == []


def _map(steam, appid, name):
    from autoproton.steam import config_store
    from autoproton.steam.vdftext import VdfDocument
    d = VdfDocument.from_bytes(steam.config.read_bytes())
    config_store.edit_compat(d, appid, CompatSetting(name) if name else None)
    steam.config.write_bytes(d.to_bytes())


def test_recovery_with_a_file_changed_since_says_so_and_keeps_history(ctx, steam):
    store = Store(ctx)
    old, _ = _fake_journal(ctx, store, steam, "other")
    _map(steam, 1091500, "proton_10")  # config.vdf now holds a setting that is neither before nor after
    msg = transaction.recover(ctx, store)
    # the file still in its "new" state is put back; the one changed since is left alone
    assert steam.localconfig.read_bytes() == old
    assert "partly in effect" in msg and "config.vdf" in msg
    assert [e.id for e in store.history.entries()] == ["e1"]  # still undoable from History


def test_recovery_judges_by_the_settings_in_effect(ctx, steam):
    store = Store(ctx)
    old, _ = _fake_journal(ctx, store, steam, "other")
    # config.vdf changed since, but not for this game: the change is fully undone
    msg = transaction.recover(ctx, store)
    assert steam.localconfig.read_bytes() == old
    assert "was undone" in msg and "partly" not in msg
    assert store.history.entries() == []


def test_recovery_of_a_change_that_is_fully_in_effect_keeps_it(ctx, steam):
    store = Store(ctx)
    _, new_local = _fake_journal(ctx, store, steam, "new")
    steam.localconfig.write_bytes(new_local + b"\n")  # Steam rewrote it since, keeping the new options
    msg = transaction.recover(ctx, store)
    assert "Finished" in msg
    assert [e.id for e in store.history.entries()] == ["e1"]


def test_recovery_repairs_half_written_change(ctx, steam):
    store = Store(ctx)
    old, new_local = _fake_journal(ctx, store, steam, "new")
    # make it look half-written: the journal also lists config.vdf, still untouched
    journal = json.loads(store.journal_path.read_text())
    journal["files"].append({"path": str(steam.config.resolve()), "old": atomic.file_sha256(steam.config), "new": "y"})
    store.journal_path.write_text(json.dumps(journal))
    msg = transaction.recover(ctx, store)
    assert "was undone" in msg
    assert steam.localconfig.read_bytes() == old
    assert store.history.entries() == []


def test_undo_works_when_original_launch_options_contain_a_tab(ctx, steam):
    text = steam.localconfig.read_text().replace(
        '"WINEDLLOVERRIDES=\\"winmm,version=n,b\\" %command%"', '"PROTON_LOG=1\\t%command%"')
    steam.localconfig.write_text(text)
    original = steam.localconfig.read_bytes()
    store = Store(ctx)
    plan = plan_for(ctx)
    assert plan.before.launch_options == "PROTON_LOG=1\t%command%"
    entry = transaction.execute(ctx, store, ChangePlan(**{**plan.__dict__, "after": GameSettings(
        "PROTON_LOG=1\t%command% --launcher-skip", None)}))
    undo(ctx, store, entry)
    assert steam.localconfig.read_bytes() == original


def test_undo_of_a_game_with_no_block_restores_exact_bytes(ctx, steam):
    store = Store(ctx)
    original_local, original_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    # 1174180 has no block in localconfig.vdf and no mapping in config.vdf
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=1174180, after=GameSettings("-x", CompatSetting("proton_10"))))
    assert set(entry.missing) == {"app_block"}  # CompatToolMapping was already there
    undo(ctx, store, entry)
    assert steam.localconfig.read_bytes() == original_local
    assert steam.config.read_bytes() == original_cfg


def test_undo_removes_a_compat_block_it_created(ctx, home, steam):
    steam.config.write_text('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n\t\t\t}\n\t\t}\n\t}\n}\n')
    original = steam.config.read_bytes()
    store = Store(ctx)
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings(None, CompatSetting("proton_10"))))
    assert b"CompatToolMapping" in steam.config.read_bytes()
    assert store.history.get(entry.id).missing == ("compat_block",)  # it's saved with the entry
    undo(ctx, store, entry)
    assert steam.config.read_bytes() == original


def test_undo_keeps_empty_blocks_that_were_already_there(ctx, steam):
    # 570's app block and an empty CompatToolMapping exist before autoproton touches them
    steam.localconfig.write_text(steam.localconfig.read_text().replace(
        '\t\t\t\t\t\t"LastPlayed"\t\t"1700000001"\n\t\t\t\t\t\t"Playtime"\t\t"42"\n', ""))
    steam.config.write_text('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n'
                            '\t\t\t{\n\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n')
    original_local, original_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    assert b'"570"\n\t\t\t\t\t{\n\t\t\t\t\t}' in original_local
    store = Store(ctx)
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings("-novid", CompatSetting("proton_10"))))
    assert entry.missing == ()
    undo(ctx, store, entry)
    assert steam.localconfig.read_bytes() == original_local
    assert steam.config.read_bytes() == original_cfg


def test_clearing_the_only_mapping_and_undoing_it_is_exact(ctx, steam):
    steam.config.write_text(steam.config.read_text().replace(
        '\t\t\t\t\t"0"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_experimental"\n\t\t\t\t\t\t"config"\t\t""\n'
        '\t\t\t\t\t\t"priority"\t\t"75"\n\t\t\t\t\t}\n', ""))
    original = steam.config.read_bytes()
    store = Store(ctx)
    before = plan_for(ctx, appid=1245620).before
    assert before.compat_tool == CompatSetting("proton_9", "", "250")
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=1245620, after=GameSettings(before.launch_options, None)))
    assert b"CompatToolMapping" in steam.config.read_bytes()  # an apply never removes the block
    undo(ctx, store, entry)
    assert steam.config.read_bytes() == original


def test_clearing_launch_options_written_twice_in_one_block(ctx, steam):
    # a hand edit left two LaunchOptions in 1091500's block; Steam reads the first
    text = steam.localconfig.read_text()
    first = '\t\t\t\t\t\t"LaunchOptions"\t\t"WINEDLLOVERRIDES=\\"winmm,version=n,b\\" %command%"\n'
    assert first in text
    steam.localconfig.write_text(text.replace(first, first + '\t\t\t\t\t\t"LaunchOptions"\t\t"-other"\n'))
    store = Store(ctx)
    transaction.execute(ctx, store, plan_for(ctx, after=GameSettings(None, None)))
    assert transaction.current_settings(*_inst_acct(ctx), 1091500).launch_options is None
    assert b"-other" not in steam.localconfig.read_bytes()


def test_undo_with_a_mapping_written_twice_in_one_block(ctx, steam):
    # a hand edit left 570 twice in CompatToolMapping: Steam reads the first, which forces no tool
    text = steam.config.read_text()
    block = '\t\t\t\t"CompatToolMapping"\n\t\t\t\t{\n'
    dup = ('\t\t\t\t\t"570"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t""\n\t\t\t\t\t}\n'
           '\t\t\t\t\t"570"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_8"\n\t\t\t\t\t}\n')
    steam.config.write_text(text.replace(block, block + dup))
    store = Store(ctx)
    assert plan_for(ctx, appid=570).before.compat_tool is None
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings(None, CompatSetting("proton_10"))))
    undo(ctx, store, entry)
    assert transaction.current_settings(*_inst_acct(ctx), 570).compat_tool is None
    assert b"proton_9" in steam.config.read_bytes()  # other games' mappings stay


def test_undoing_an_older_change_gives_back_the_original_files(ctx, steam):
    original_local, original_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    store = Store(ctx)
    # 1174180 has no block in localconfig.vdf; the first change only forces a tool,
    # the second adds launch options and so creates the game's block
    a = transaction.execute(ctx, store, plan_for(ctx, appid=1174180, after=GameSettings(None, CompatSetting("proton_10"))))
    transaction.execute(ctx, store, plan_for(ctx, appid=1174180, after=GameSettings("-b", CompatSetting("proton_10"))))
    undo(ctx, store, a)
    assert steam.localconfig.read_bytes() == original_local
    assert steam.config.read_bytes() == original_cfg


def test_undoing_an_older_change_removes_a_mapping_block_a_newer_one_added(ctx, steam):
    steam.config.write_text('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n\t\t\t}\n\t\t}\n\t}\n}\n')
    original_local, original_cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    store = Store(ctx)
    a = transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings("-a", None)))
    transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings("-a", CompatSetting("proton_10"))))
    undo(ctx, store, a)
    assert steam.localconfig.read_bytes() == original_local
    assert steam.config.read_bytes() == original_cfg


def _inst_acct(ctx):
    inst = locate.find_installs(ctx)[0]
    return inst, locate.accounts(inst)[0]


def test_undo_with_case_variant_duplicate_blocks_is_exact(ctx, steam):
    # a second, hidden "Apps" block after the one Steam reads
    text = steam.localconfig.read_text()
    marker = '\t\t\t\t"apps"\n'
    hidden = '\t\t\t\t"Apps"\n\t\t\t\t{\n\t\t\t\t\t"570"\n\t\t\t\t\t{\n\t\t\t\t\t\t"LaunchOptions"\t\t"hidden"\n\t\t\t\t\t}\n\t\t\t\t}\n'
    end = text.index("\n\t\t\t}\n", text.index(marker)) + 1  # end of the "Steam" block
    steam.localconfig.write_text(text[:end] + hidden + text[end:])
    original = steam.localconfig.read_bytes()
    from autoproton.steam.vdftext import VdfDocument
    copies = VdfDocument.from_bytes(original).find_all(("UserLocalConfigStore", "Software", "Valve", "Steam", "apps", "570"))
    assert len(copies) == 2  # both spellings sit side by side under "Steam"
    store = Store(ctx)
    assert plan_for(ctx, appid=570).before.launch_options is None  # Steam doesn't see "hidden"
    entry = transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings("-novid", None)))
    assert b'"hidden"' in steam.localconfig.read_bytes()  # the copy Steam ignores is left alone
    undo(ctx, store, entry)
    assert steam.localconfig.read_bytes() == original


def test_backups_pruned_but_pristine_kept(ctx, steam):
    t = [1000.0]
    ctx.clock = lambda: t[0]
    store = Store(ctx, keep_backups=2)
    original = steam.localconfig.read_bytes()
    for i in range(4):
        t[0] += 60
        transaction.execute(ctx, store, plan_for(ctx, appid=570, after=GameSettings(f"-n{i}", None)))
    sets = store.backups.list()
    regular = [s for s in sets if not s.id.startswith("pristine/")]
    pristine = [s for s in sets if s.id.startswith("pristine/")]
    assert len(regular) == 2
    # only localconfig.vdf ever changed, so there is one pristine copy: the original file
    (p_local,) = pristine
    assert store.backups.read(p_local, "localconfig") == original


def test_restore_file(ctx, steam):
    store = Store(ctx)
    original = steam.localconfig.read_bytes()
    entry = transaction.execute(ctx, store, plan_for(ctx))
    backup = store.backups.get(entry.backup_set)
    plan = plan_for(ctx)
    res = transaction.restore_file(ctx, store, backup, "localconfig", plan.install, plan.account)
    assert steam.localconfig.read_bytes() == original
    safety = store.backups.get(res.backup_set)
    assert store.backups.read(safety, "localconfig") != original  # the pre-restore state was kept


def test_history_marks_and_torn_lines(tmp_path):
    h = History(tmp_path / "h.jsonl", clock=lambda: 5.0)
    from autoproton.history import HistoryEntry
    e = h.append(HistoryEntry("a", 1, "apply", "/s", "1", 10, "G", GameSettings(), GameSettings("x")))
    with open(h.path, "a") as f:
        f.write('{"type": "change", "id": "broken"')  # torn write, no newline
    h.mark("a", "works")
    (got,) = h.entries()
    assert got.id == e.id and got.mark == "works"
    with pytest.raises(ValueError):
        h.mark("a", "great")
