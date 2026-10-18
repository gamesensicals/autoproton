"""Split launch-option strings into pieces, check them, and put them back together.

A Steam launch option looks like ``ENV=1 OTHER=2 wrapper %command% -arg value``.
Steam replaces ``%command%`` with the game's command line and runs the whole
thing through a shell, so anything taken from a ProtonDB report is checked
strictly before autoproton will offer it: only known wrapper programs, no
shell syntax, no variables that point at files or change how programs load.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from . import knowledge

COMMAND = "%command%"
_EMPTY = {"", "none", "n/a", "na", "no", "-", "nothing", "default", "nope", "null", COMMAND}
_PLAIN = re.compile(r"^[A-Za-z0-9_.,:=+\-@%]+$")


@dataclass(frozen=True)
class Component:
    kind: str  # "env" | "wrapper" | "arg"
    key: str  # variable name, program name, or flag
    value: str | None = None  # variable value, flag value, or the wrapper's own arguments
    extra: tuple[str, ...] = ()  # wrapper arguments as separate words

    @property
    def vote_key(self) -> tuple[str, str]:
        if self.kind == "env":
            return ("env", self.key)
        return (self.kind, self.key.casefold())

    def words(self) -> list[str]:
        if self.kind == "env":
            v = self.value or ""
            if v == "" or _PLAIN.match(v):
                return [f"{self.key}={v}"]
            return [f'{self.key}="{v}"']
        if self.kind == "wrapper":
            return [self.key, *self.extra]
        return [self.key] + ([self.value] if self.value else [])

    @property
    def text(self) -> str:
        return " ".join(self.words())


@dataclass
class Parsed:
    env: list[Component] = field(default_factory=list)
    wrappers: list[Component] = field(default_factory=list)
    args: list[Component] = field(default_factory=list)
    has_command: bool = False
    ok: bool = True  # False when the string couldn't be split (unbalanced quotes)
    rejected: list[str] = field(default_factory=list)

    @property
    def components(self) -> list[Component]:
        return self.env + self.wrappers + self.args

    @property
    def empty(self) -> bool:
        return not (self.env or self.wrappers or self.args)


def _canonical_dll_overrides(value: str) -> str:
    """Sort WINEDLLOVERRIDES entries so equivalent spellings vote together."""
    entries = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        dlls, sep, mode = part.partition("=")
        names = ",".join(sorted(d.strip().lower().removesuffix(".dll") for d in dlls.split(",") if d.strip()))
        # keep the "=" even with no mode: "d3d12=" disables d3d12, plain "d3d12" is ignored by Wine
        entries.append(f"{names}={mode.strip().replace(' ', '')}" if sep else names)
    return ";".join(sorted(entries))


def _safe_word(word: str) -> bool:
    return not any(c in word for c in "`$\\\"'<>|&;(){}*?!~#\n\r\t ") and word.isprintable()


def parse(text: str | None, strict: bool = True) -> Parsed:
    """Split a launch-options string.

    ``strict`` is for text from reports: unknown programs and anything that
    looks unsafe are dropped (and listed in ``rejected``). Without it, every
    word is kept, for reading the user's own launch options.
    """
    out = Parsed()
    raw = (text or "").strip()
    if raw.lower() in _EMPTY:
        out.has_command = raw == COMMAND
        return out
    try:
        words = shlex.split(raw, posix=True)
    except ValueError:
        out.ok = False
        return out
    if words and words[0] == "env":
        words = words[1:]
    if strict:
        # "%command%" glued to something else would put a second command in the string
        glued = [w for w in words if COMMAND in w and w != COMMAND]
        out.rejected.extend(glued)
        words = [w for w in words if w not in glued]

    if COMMAND in words:
        out.has_command = True
        i = words.index(COMMAND)
        pre, post = words[:i], [w for w in words[i + 1:] if w != COMMAND]
    else:
        pre, post = [], []
        for w in words:
            name = w.rsplit("/", 1)[-1]
            if knowledge.ENV_NAME.match(w.split("=", 1)[0]) and "=" in w and not w.startswith("-"):
                pre.append(w)
            elif name in knowledge.WRAPPERS:
                pre.append(w)
            else:
                post.append(w)

    i = 0
    while i < len(pre):
        w = pre[i]
        i += 1
        if "=" in w and knowledge.ENV_NAME.match(w.split("=", 1)[0]):
            key, value = w.split("=", 1)
            if strict:
                if (not knowledge.env_allowed(key) or not knowledge.SAFE_ENV_VALUE.match(value)
                        or "/" in value or knowledge.runs_commands(value)):
                    out.rejected.append(w)
                    continue
                if key == "WINEDLLOVERRIDES":
                    value = _canonical_dll_overrides(value)
            out.env.append(Component("env", key, value))
            continue
        if w == "--":
            continue
        name = w.rsplit("/", 1)[-1]
        if strict and name not in knowledge.WRAPPERS:
            out.rejected.append(w)
            # skip this program's own arguments too
            while i < len(pre) and pre[i].startswith("-") and pre[i] != "--":
                i += 1
            continue
        extra = []
        unsafe = False
        until_dashdash = name == "gamescope"
        while i < len(pre):
            nxt = pre[i]
            if nxt == "--":
                i += 1
                if until_dashdash:
                    extra.append("--")
                break
            if until_dashdash and "=" in nxt and knowledge.ENV_NAME.match(nxt.split("=", 1)[0]):
                break
            if not nxt.startswith("-"):
                if not until_dashdash or strict:
                    break  # another program (checked on its own), or in strict mode the end of gamescope's options
            extra.append(nxt)
            i += 1
            if strict and not knowledge.SAFE_FLAG.match(nxt):
                unsafe = True
            if (strict and until_dashdash and nxt in knowledge.GAMESCOPE_VALUE_FLAGS and i < len(pre)
                    and not pre[i].startswith("-")):
                value = pre[i]
                i += 1
                extra.append(value)
                if not knowledge.SAFE_ARG_VALUE.match(value):
                    unsafe = True
        if until_dashdash and extra[-1:] != ["--"]:
            extra.append("--")
        if strict and (unsafe or not all(_safe_word(x) for x in extra)):
            out.rejected.append(" ".join([w, *extra]))
            continue
        out.wrappers.append(Component("wrapper", name if strict else w, " ".join(extra) or None, tuple(extra)))

    i = 0
    while i < len(post):
        w = post[i]
        i += 1
        if w[:1] in "-+" and len(w) > 1:
            value = None
            if i < len(post) and post[i][:1] not in "-+" and "=" not in post[i]:
                value = post[i]
                i += 1
            if strict and (not knowledge.SAFE_FLAG.match(w) or (value is not None and not knowledge.SAFE_ARG_VALUE.match(value))):
                out.rejected.append(" ".join([w] + ([value] if value else [])))
                continue
            out.args.append(Component("arg", w, value))
        elif strict:
            out.rejected.append(w)
        else:
            out.args.append(Component("arg", w, None))
    return out


def assemble(components: list[Component]) -> str:
    """Build a launch-options string. Variables first, then wrappers, then %command%, then arguments."""
    env = [c for c in components if c.kind == "env"]
    wrappers = sorted((c for c in components if c.kind == "wrapper"), key=lambda c: c.key == "gamescope")
    args = [c for c in components if c.kind == "arg"]
    if not (env or wrappers or args):
        return ""
    words = [w for c in env + wrappers for w in c.words()] + [COMMAND] + [w for c in args for w in c.words()]
    return " ".join(words)


def _last_command_start(text: str) -> int:
    """Where the last simple command in ``text`` starts: just after its last ; | or & outside quotes."""
    start, quote, escaped = 0, None, False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in ";|&":
            start = i + 1
    return start


def merge(current: str | None, add: list[Component]) -> str:
    """Add ``add`` to the user's existing launch options without rewriting what they typed.

    Pieces the user already has in effect (same variable, program or flag) are
    left as they are. New variables go at the start of the command that runs the
    game, new programs just before ``%command%``, and new arguments right after
    it, so they still reach the game when the user's text continues with a pipe
    or a comment.
    """
    cur = (current or "").strip()
    if not cur or cur.lower() in _EMPTY - {COMMAND}:
        return assemble(add)
    have = parse(cur, strict=False)
    # Without %command%, Steam passes all of the user's text to the game as
    # arguments, so variables and programs written there aren't in effect.
    have_keys = {c.vote_key for c in have.components if have.has_command or c.kind == "arg"}
    new = [c for c in add if c.vote_key not in have_keys]
    env = " ".join(c.text for c in new if c.kind == "env")
    wrappers = " ".join(c.text for c in sorted((c for c in new if c.kind == "wrapper"), key=lambda c: c.key == "gamescope"))
    args = " ".join(c.text for c in new if c.kind == "arg")
    if COMMAND in cur:
        before, after = cur.split(COMMAND, 1)
        before = before.rstrip()
        cut = _last_command_start(before)
        head, tail = before[:cut].strip(), before[cut:].strip()
        command = " ".join(p for p in [head, env, tail, wrappers, COMMAND, args] if p)
        return command + after.rstrip()
    if env or wrappers:
        return " ".join(p for p in [env, wrappers, COMMAND, args, cur] if p)
    return " ".join(p for p in [cur, args] if p)
