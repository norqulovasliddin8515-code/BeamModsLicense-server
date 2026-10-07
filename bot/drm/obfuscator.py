"""
obfuscator.py — per-build polymorphic Lua obfuscation for the DRM controller.

What it does (every build is different):
  * every string literal (URL, token, public key, messages, API names) is
    encrypted with a random per-build key and decoded at runtime
  * every local variable/function/parameter is renamed to random lI1-style names
  * comments and indentation are stripped

What it can NOT do (no Lua obfuscator can): stop a determined reverser who
runs the code and dumps decrypted strings, or who simply deletes the
controller from the .jbeam. The goal is cost: no grep-able URL/token/key,
and no generic patch that works on every buyer's copy.
"""
from __future__ import annotations

import random
import re
import secrets
from dataclasses import dataclass

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if",
    "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while",
}
# Globals the templates rely on — a local with one of these names would break renaming.
RESERVED_GLOBALS = {
    "electrics", "powertrain", "guihooks", "obj", "objectId", "v", "be", "controller", "string",
    "table", "math", "os", "tostring", "tonumber", "type", "pcall", "require", "ipairs", "pairs",
    "rawget", "rawset", "_G", "jsonReadFile", "jsonWriteFile", "Steam", "Engine", "log", "select",
    "error", "unpack", "setmetatable", "getmetatable", "next", "print",
}

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>[ \t\r]+)
  | (?P<nl>\n)
  | (?P<lcomment>--\[(?P<leq>=*)\[.*?\](?P=leq)\])
  | (?P<comment>--[^\n]*)
  | (?P<lstring>\[(?P<seq>=*)\[.*?\](?P=seq)\])
  | (?P<string>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
  | (?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|::|[-+*/%^\#<>=(){}\[\];:,.])
    """,
    re.S | re.X,
)


@dataclass
class Tok:
    kind: str
    text: str
    nl_before: bool = False


def tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    pos, nl = 0, False
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise SyntaxError(f"Lua tokenizer stuck at: {src[pos:pos + 40]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind in ("leq", "seq"):
            kind = "lcomment" if m.group("lcomment") else "lstring"
        if kind == "ws" or kind in ("comment", "lcomment"):
            continue
        if kind == "nl":
            nl = True
            continue
        if kind == "lstring":
            raise SyntaxError("long strings [[...]] are not supported in templates")
        out.append(Tok(kind, m.group(0), nl))
        nl = False
    return out


_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b",
        "f": "\f", "v": "\v", "\n": "\n"}


def unquote(lit: str) -> bytes:
    body, out, i = lit[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        nxt = body[i + 1]
        if nxt.isdigit():
            m = re.match(r"\d{1,3}", body[i + 1:])
            out.append(int(m.group(0)))
            i += 1 + len(m.group(0))
        elif nxt in _ESC:
            out += _ESC[nxt].encode()
            i += 2
        else:
            raise SyntaxError(f"unsupported escape \\{nxt}")
    return bytes(out)


def _declared_locals(toks: list[Tok]) -> set[str]:
    names: set[str] = set()
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind != "name":
            continue
        if t.text == "local":
            j = i + 1
            if j < n and toks[j].text == "function":
                names.add(toks[j + 1].text)
                continue
            while j < n and toks[j].kind == "name" and toks[j].text not in KEYWORDS:
                names.add(toks[j].text)
                if j + 1 < n and toks[j + 1].text == ",":
                    j += 2
                else:
                    break
        elif t.text == "function":
            j = i + 1
            while j < n and toks[j].text != "(":
                j += 1
            j += 1
            while j < n and toks[j].text != ")":
                if toks[j].kind == "name":
                    names.add(toks[j].text)
                j += 1
        elif t.text == "for":
            j = i + 1
            while j < n and toks[j].text not in ("in", "="):
                if toks[j].kind == "name":
                    names.add(toks[j].text)
                j += 1
    return names


class _NameGen:
    def __init__(self, rng: random.Random):
        self.rng, self.used = rng, set()

    def __call__(self) -> str:
        while True:
            name = self.rng.choice("lI") + "".join(self.rng.choice("lI1") for _ in range(self.rng.randint(7, 11)))
            if name not in self.used:
                self.used.add(name)
                return name


def obfuscate(src: str, seed: int | None = None) -> str:
    """Return obfuscated Lua source. `src` must follow the template rules
    (table keys written as ["key"], no [[long strings]], calls with parentheses)."""
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    toks = tokenize(src)
    gen = _NameGen(rng)

    locals_ = _declared_locals(toks)
    clash = locals_ & RESERVED_GLOBALS
    if clash:
        raise ValueError(f"template declares locals that shadow globals: {sorted(clash)}")
    rename = {name: gen() for name in sorted(locals_)}

    # ---- string encryption ----
    key = [rng.randrange(256) for _ in range(rng.randint(24, 40))]
    mul = rng.choice([3, 5, 7, 11, 13])
    strings: list[bytes] = []
    index: dict[bytes, int] = {}
    dec_fn, tbl, cache, key_tbl = gen(), gen(), gen(), gen()

    def enc_ref(raw: bytes) -> str:
        if raw not in index:
            index[raw] = len(strings) + 1
            strings.append(raw)
        return f"{dec_fn}({index[raw]})"

    out: list[str] = []
    prev: Tok | None = None
    for i, t in enumerate(toks):
        text = t.text
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.kind == "op" and text == "." and nxt is not None and nxt.kind == "name":
            prev = t            # a.b  ->  a[DEC(n)]  (hides API names like guihooks.message)
            continue
        if t.kind == "name" and prev is not None and prev.text == ".":
            text = "[" + enc_ref(text.encode()) + "]"
        elif t.kind == "name" and text in rename:
            if not (prev is not None and prev.text == ":"):
                text = rename[text]
        elif t.kind == "string":
            text = enc_ref(unquote(text))
        out.append(("\n" if t.nl_before else "") + text)
        prev = t

    def encode(idx: int, raw: bytes) -> str:
        klen = len(key)
        vals = [(b + key[(j + idx) % klen] + j * mul) % 256 for j, b in enumerate(raw)]
        return "{" + ",".join(map(str, vals)) + "}"

    i_, n_, b_, j_, s_ = gen(), gen(), gen(), gen(), gen()
    header = (
        f"local {key_tbl}={{{','.join(map(str, key))}}}\n"
        f"local {tbl}={{{','.join(encode(i + 1, s) for i, s in enumerate(strings))}}}\n"
        f"local {cache}={{}}\n"
        f"local function {dec_fn}({i_})\n"
        f"local {s_}={cache}[{i_}] if {s_} then return {s_} end\n"
        f"local {b_}={tbl}[{i_}] local {n_}=#{key_tbl} {s_}={{}}\n"
        f"for {j_}=1,#{b_} do {s_}[{j_}]=string.char(({b_}[{j_}]-{key_tbl}[(({j_}-1)+{i_})%{n_}+1]-({j_}-1)*{mul})%256) end\n"
        f"{s_}=table.concat({s_}) {cache}[{i_}]={s_} return {s_}\n"
        f"end\n"
    )
    return header + _join(out)


def _join(parts: list[str]) -> str:
    """Join tokens with the minimum whitespace Lua needs."""
    res: list[str] = []
    for p in parts:
        if res and not p.startswith("\n"):
            a, b = res[-1][-1], p[0]
            if (a.isalnum() or a == "_") and (b.isalnum() or b == "_"):
                res.append(" ")
            elif a == "-" and b == "-":
                res.append(" ")
            elif a == "." and (b.isdigit() or b == "."):
                res.append(" ")
            elif a.isdigit() and b == ".":
                res.append(" ")
        res.append(p)
    return "".join(res)
