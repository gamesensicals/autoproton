import pytest
import vdf as vdf_oracle
from fakesteam import CONFIG, GE_VDF, LOCALCONFIG

from autoproton.steam.vdftext import VdfDocument, VdfError, encode

APPS = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps")


def doc(text):
    return VdfDocument.from_bytes(text.encode())


@pytest.mark.parametrize("text", [LOCALCONFIG, CONFIG, GE_VDF.replace("{name}", "GE-Proton10-34")])
def test_round_trip_is_byte_identical(text):
    assert doc(text).to_bytes() == text.encode()


def test_reads_escapes_and_matches_reference_parser():
    d = doc(LOCALCONFIG)
    assert d.get((*APPS, "1091500", "LaunchOptions")) == 'WINEDLLOVERRIDES="winmm,version=n,b" %command%'
    assert d.get(("UserLocalConfigStore", "friends", "999", "name")) == 'Bob "the" builder'
    assert d.get(("UserLocalConfigStore", "friends", "999", "tag")) == "a\\b"
    ref = vdf_oracle.loads(LOCALCONFIG)
    assert d.find(["UserLocalConfigStore"]).to_dict() == ref["UserLocalConfigStore"]


def test_lookup_is_case_insensitive():
    d = doc(LOCALCONFIG)
    assert d.get(("userlocalconfigstore", "SOFTWARE", "valve", "steam", "APPS", "570", "playtime")) == "42"


def test_set_existing_value_changes_only_that_value():
    d = doc(LOCALCONFIG)
    d.set((*APPS, "1091500", "LaunchOptions"), 'A="b c" %command% --launcher-skip')
    new = d.to_bytes().decode()
    assert new.replace('"A=\\"b c\\" %command% --launcher-skip"', '"WINEDLLOVERRIDES=\\"winmm,version=n,b\\" %command%"') == LOCALCONFIG
    assert doc(new).get((*APPS, "1091500", "LaunchOptions")) == 'A="b c" %command% --launcher-skip'


def test_insert_new_key_uses_sibling_indentation():
    d = doc(LOCALCONFIG)
    d.set((*APPS, "570", "LaunchOptions"), "-novid")
    new = d.to_bytes().decode()
    assert '\t\t\t\t\t\t"Playtime"\t\t"42"\n\t\t\t\t\t\t"LaunchOptions"\t\t"-novid"\n\t\t\t\t\t}' in new
    assert vdf_oracle.loads(new)["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]["570"]["LaunchOptions"] == "-novid"
    # everything except the inserted line is unchanged
    assert new.replace('\t\t\t\t\t\t"LaunchOptions"\t\t"-novid"\n', "") == LOCALCONFIG


def test_insert_creates_missing_blocks():
    d = doc(LOCALCONFIG)
    d.set((*APPS, "1245620", "LaunchOptions"), "%command% -dx11")
    new = d.to_bytes().decode()
    ref = vdf_oracle.loads(new)
    assert ref["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]["1245620"] == {"LaunchOptions": "%command% -dx11"}
    assert '\t\t\t\t\t"1245620"\n\t\t\t\t\t{\n\t\t\t\t\t\t"LaunchOptions"\t\t"%command% -dx11"\n\t\t\t\t\t}\n' in new


def test_insert_whole_missing_path():
    d = doc('"UserLocalConfigStore"\n{\n\t"friends"\n\t{\n\t}\n}\n')
    d.set((*APPS, "10", "LaunchOptions"), "x")
    assert vdf_oracle.loads(d.text)["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]["10"]["LaunchOptions"] == "x"


def test_delete_removes_whole_lines():
    d = doc(CONFIG)
    assert d.delete(("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping", "1245620")) == 1
    new = d.to_bytes().decode()
    assert "1245620" not in new
    assert '"0"' in new
    assert vdf_oracle.loads(new)["InstallConfigStore"]["Software"]["Valve"]["Steam"]["CompatToolMapping"] == {
        "0": {"name": "proton_experimental", "config": "", "priority": "75"}
    }


def test_delete_missing_is_noop():
    d = doc(CONFIG)
    assert d.delete(("InstallConfigStore", "nope")) == 0
    assert d.to_bytes() == CONFIG.encode()


def test_case_variant_duplicates_are_all_updated():
    text = '"S"\n{\n\t"Apps"\n\t{\n\t\t"1"\n\t\t{\n\t\t\t"LaunchOptions"\t\t"old"\n\t\t}\n\t}\n\t"apps"\n\t{\n\t\t"1"\n\t\t{\n\t\t\t"LaunchOptions"\t\t"old2"\n\t\t}\n\t}\n}\n'
    d = doc(text)
    d.set(("S", "apps", "1", "LaunchOptions"), "new")
    assert [n.value for n in d.find_all(("S", "apps", "1", "LaunchOptions"))] == ["new", "new"]
    d.delete(("S", "apps", "1", "LaunchOptions"))
    assert d.find_all(("S", "apps", "1", "LaunchOptions")) == []


def test_crlf_bom_and_comments_are_preserved():
    text = '﻿"a"\r\n{\r\n\t"b"\t\t"1" // keep me\r\n}\r\n'
    raw = text.encode("utf-8")
    d = VdfDocument.from_bytes(raw)
    assert d.to_bytes() == raw
    d.set(("a", "c"), "2")
    out = d.to_bytes()
    assert out.startswith(b"\xef\xbb\xbf")
    assert b'\t"c"\t\t"2"\r\n}' in out
    assert b"// keep me" in out


def test_invalid_bytes_round_trip():
    raw = b'"a"\n{\n\t"b"\t\t"\xff\xfe"\n}\n'
    assert VdfDocument.from_bytes(raw).to_bytes() == raw


@pytest.mark.parametrize("bad", ['"a"\n{\n', '"a"\n}\n', '"a"', '"a" {\n "b" \n}'])
def test_malformed_input_raises(bad):
    with pytest.raises(VdfError):
        doc(bad)


def test_conditionals_are_skipped():
    d = doc('"a"\n{\n\t"b"\t"1"\t[$WIN32]\n\t"c"\t"2"\n}\n')
    assert d.get(("a", "c")) == "2"


def test_encode_escapes_like_steam():
    assert encode('a"b\\c') == '"a\\"b\\\\c"'
