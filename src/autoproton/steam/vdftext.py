"""Reader and editor for Valve's text KeyValues ("VDF") files.

Steam rewrites files like localconfig.vdf on its own, and they hold far more
than the two settings autoproton changes. So instead of loading the whole file
into a dict and writing it back out (which re-escapes and re-indents
everything), this module remembers where every key and value sits in the
original text and splices in only the bytes that change. Everything else in
the file stays byte-for-byte identical.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


class VdfError(ValueError):
    """The text is not valid VDF."""


_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>//[^\n]*)
    | (?P<quoted>"(?:[^"\\]|\\.)*")
    | (?P<open>\{)
    | (?P<close>\})
    | (?P<cond>\[[^\]\n]*\])
    | (?P<bare>[^\s{}"\[\]]+)
    """,
    re.VERBOSE | re.DOTALL,
)

_UNESCAPE = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


@dataclass
class Node:
    """One key in the file: either a leaf with a value or a block with children."""

    key: str
    key_start: int
    key_end: int
    value: str | None = None
    value_start: int = -1
    value_end: int = -1
    children: list[Node] | None = None
    close_pos: int = -1
    end: int = 0

    @property
    def is_block(self) -> bool:
        return self.children is not None

    def find_all(self, key: str) -> list[Node]:
        k = key.casefold()
        return [c for c in (self.children or []) if c.key.casefold() == k]

    def child(self, key: str) -> Node | None:
        found = self.find_all(key)
        return found[0] if found else None

    def get(self, key: str, default: str | None = None) -> str | None:
        """Value of the first leaf child called ``key`` (case-insensitive)."""
        for c in self.find_all(key):
            if not c.is_block:
                return c.value
        return default

    def to_dict(self) -> dict:
        """Plain dict of this block. First occurrence wins for duplicate keys."""
        out: dict = {}
        for c in self.children or []:
            if c.key in out:
                continue
            out[c.key] = c.to_dict() if c.is_block else c.value
        return out


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _decode(text: str, kind: str, start: int, end: int) -> str:
    raw = text[start:end]
    if kind != "quoted":
        return raw
    inner = raw[1:-1]
    if "\\" not in inner:
        return inner
    return re.sub(r"\\(.)", lambda m: _UNESCAPE.get(m.group(1), m.group(0)), inner, flags=re.DOTALL)


def encode(value: str) -> str:
    """Quote a string the way Steam writes it."""
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    pos, n = 0, len(text)
    match = _TOKEN.match
    while pos < n:
        m = match(text, pos)
        if m is None:
            raise VdfError(f"unexpected character {text[pos]!r} on line {_line_of(text, pos)}")
        kind = m.lastgroup
        if kind != "ws" and kind != "comment":
            tokens.append((kind, m.start(), m.end()))
        pos = m.end()
    return tokens


def parse(text: str) -> list[Node]:
    """Parse VDF text into a list of top-level nodes."""
    toks = _tokenize(text)
    count = len(toks)
    i = 0

    def entries(depth: int) -> list[Node]:
        nonlocal i
        nodes: list[Node] = []
        while i < count:
            kind, s, e = toks[i]
            if kind == "close":
                if depth == 0:
                    raise VdfError(f"unexpected '}}' on line {_line_of(text, s)}")
                return nodes
            if kind not in ("quoted", "bare"):
                raise VdfError(f"expected a key on line {_line_of(text, s)}")
            node = Node(_decode(text, kind, s, e), s, e)
            i += 1
            if i < count and toks[i][0] == "cond":
                i += 1
            if i >= count:
                raise VdfError(f"key {node.key!r} has no value (line {_line_of(text, s)})")
            kind2, s2, e2 = toks[i]
            if kind2 == "open":
                i += 1
                node.children = entries(depth + 1)
                if i >= count:
                    raise VdfError(f"block {node.key!r} is never closed (line {_line_of(text, s)})")
                node.close_pos, node.end = toks[i][1], toks[i][2]
                i += 1
            elif kind2 in ("quoted", "bare"):
                node.value = _decode(text, kind2, s2, e2)
                node.value_start, node.value_end, node.end = s2, e2, e2
                i += 1
            else:
                raise VdfError(f"key {node.key!r} has no value (line {_line_of(text, s)})")
            if i < count and toks[i][0] == "cond":
                node.end = toks[i][2]
                i += 1
            nodes.append(node)
        if depth > 0:
            raise VdfError("a block is never closed before the end of the file")
        return nodes

    return entries(0)


class VdfDocument:
    """A parsed VDF file that can be edited in place."""

    def __init__(self, text: str, bom: bool = False):
        self.text = text
        self.bom = bom
        self.root = Node("", 0, 0, children=parse(text), close_pos=len(text), end=len(text))

    # ---- loading and saving -------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> VdfDocument:
        bom = data.startswith(b"\xef\xbb\xbf")
        if bom:
            data = data[3:]
        return cls(data.decode("utf-8", "surrogateescape"), bom)

    @classmethod
    def from_file(cls, path) -> VdfDocument:
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    def to_bytes(self) -> bytes:
        data = self.text.encode("utf-8", "surrogateescape")
        return b"\xef\xbb\xbf" + data if self.bom else data

    @property
    def newline(self) -> str:
        return "\r\n" if "\r\n" in self.text else "\n"

    # ---- reading ------------------------------------------------------------

    def find_all(self, path: Sequence[str]) -> list[Node]:
        """Every node at ``path``, matching keys case-insensitively, in file order."""
        level = [self.root]
        for comp in path:
            level = [c for n in level if n.is_block for c in n.find_all(comp)]
            if not level:
                break
        return level

    def find(self, path: Sequence[str]) -> Node | None:
        found = self.find_all(path)
        return found[0] if found else None

    def get(self, path: Sequence[str]) -> str | None:
        """First leaf value at ``path`` (the one Steam itself reads), or None."""
        for node in self.find_all(path):
            if not node.is_block:
                return node.value
        return None

    # ---- editing ------------------------------------------------------------

    def set(self, path: Sequence[str], value: str) -> None:
        """Set the leaf at ``path``, creating any missing blocks on the way.

        If the file holds the same key in several spellings (``apps`` and
        ``Apps``), every copy is updated so Steam sees the same value whichever
        one it reads.
        """
        if not path:
            raise ValueError("empty path")
        parents = self.find_all(path[:-1]) if len(path) > 1 else [self.root]
        parents = [p for p in parents if p.is_block]
        if parents:
            leaves = [c for p in parents for c in p.find_all(path[-1])]
            if leaves:
                ops = []
                for leaf in leaves:
                    if leaf.is_block:
                        raise VdfError(f"{'/'.join(path)} is a block, not a value")
                    ops.append((leaf.value_start, leaf.value_end, encode(value)))
                self._apply(ops)
                return
            self._apply([self._insert_op(parents[0], list(path[-1:]), value)])
            return
        node, depth = self.root, 0
        for comp in path[:-1]:
            nxt = next((c for c in node.find_all(comp) if c.is_block), None)
            if nxt is None:
                break
            node, depth = nxt, depth + 1
        self._apply([self._insert_op(node, list(path[depth:]), value)])

    def delete(self, path: Sequence[str]) -> int:
        """Remove every node at ``path``. Returns how many were removed."""
        if not path:
            raise ValueError("empty path")
        nodes = self.find_all(path)
        ops = [self._delete_op(n) for n in nodes]
        if ops:
            self._apply(ops)
        return len(ops)

    # ---- internals ----------------------------------------------------------

    def _line_start(self, pos: int) -> int:
        return self.text.rfind("\n", 0, pos) + 1

    def _indent_before(self, pos: int) -> str | None:
        """Whitespace between the start of the line and ``pos``, or None if there is text there."""
        prefix = self.text[self._line_start(pos):pos]
        return prefix if prefix.strip() == "" else None

    def _child_indent(self, parent: Node) -> str:
        for child in parent.children or []:
            indent = self._indent_before(child.key_start)
            if indent is not None:
                return indent
        if parent is self.root:
            return ""
        return (self._indent_before(parent.key_start) or "") + "\t"

    def _insert_op(self, parent: Node, keys: list[str], value: str) -> tuple[int, int, str]:
        nl = self.newline
        indent = self._child_indent(parent)

        def build(rest: list[str], ind: str) -> str:
            if len(rest) == 1:
                return f"{ind}{encode(rest[0])}\t\t{encode(value)}{nl}"
            return f"{ind}{encode(rest[0])}{nl}{ind}{{{nl}{build(rest[1:], ind + chr(9))}{ind}}}{nl}"

        snippet = build(keys, indent)
        if parent is self.root:
            pos = len(self.text)
            if self.text and not self.text.endswith("\n"):
                snippet = nl + snippet
            return (pos, pos, snippet)
        pos = parent.close_pos
        if self._indent_before(pos) is not None:
            start = self._line_start(pos)
            return (start, start, snippet)
        parent_indent = self._indent_before(parent.key_start) or ""
        return (pos, pos, nl + snippet + parent_indent)

    def _delete_op(self, node: Node) -> tuple[int, int, str]:
        start, end = node.key_start, node.end
        if self._indent_before(start) is not None:
            line_end = self.text.find("\n", end)
            line_end = len(self.text) if line_end == -1 else line_end + 1
            if self.text[end:line_end].strip() == "":
                return (self._line_start(start), line_end, "")
        return (start, end, "")

    def _apply(self, ops: Iterable[tuple[int, int, str]]) -> None:
        text = self.text
        last = None
        for start, end, repl in sorted(ops, key=lambda o: o[0], reverse=True):
            if last is not None and end > last:
                raise VdfError("overlapping edits")
            text = text[:start] + repl + text[end:]
            last = start
        self.text = text
        self.root = Node("", 0, 0, children=parse(text), close_pos=len(text), end=len(text))
