#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Noi922 – https://github.com/Noi922/Linux-plugin
"""hamster – eine freundliche Shell im Stil von fish.

* Farbige Syntax-Hervorhebung beim Tippen (rot = Befehl existiert nicht)
* Grauer Vorschlag aus dem Verlauf (→ oder Strg+E zum Übernehmen)
* Tab-Vervollständigung für Befehle, Dateien, Variablen, git/apt/docker/...
* Fehler werden VOR dem Ausführen geprüft und mit ^^^ genau markiert
* Nach dem Ausführen wird die Fehlermeldung erklärt (Datei, Zeile, Ursache)
"""
import os
import sys

__version__ = "1.1"


def _fallback_to_bash(args, login=False):
    os.execvp("bash", ["bash"] + (["-l"] if login else []) + list(args))


# Wird hamster von anderen Programmen aufgerufen (ssh host befehl, scp, rsync, cron,
# VS Code, "hamster -c ...", "hamster skript.sh", Pipes), übernimmt echte bash.
# So bleibt hamster als Standard-Shell mit allen Programmen kompatibel.
_ARGS = sys.argv[1:]
LOGIN = sys.argv[0].startswith("-") or "SHLVL" not in os.environ
if "-l" in _ARGS or "--login" in _ARGS:
    LOGIN = True
_REST = [a for a in _ARGS if a not in ("-l", "--login", "-i")]
if _REST[:1] in (["--version"], ["-v"]):
    print("hamster " + __version__)
    sys.exit(0)
if _REST[:1] in (["--help"], ["-h"]):
    print("hamster – interaktive Shell. Ohne Argumente starten. Alles andere (z. B. -c BEFEHL, "
          "SKRIPT) wird an bash weitergegeben.\n  --try BEFEHL   Befehl mit hamster-Fehlererklärung ausführen")
    sys.exit(0)
if (_REST and _REST[0] != "--try") or (not _REST and not (sys.stdin.isatty() and sys.stdout.isatty())):
    _fallback_to_bash(_REST, LOGIN)

try:
    import atexit
    import difflib
    import fcntl
    import pty
    import select
    import re
    import shlex
    import shutil
    import signal
    import socket
    import subprocess
    import tempfile
    import termios
    import threading
    import time
    from dataclasses import dataclass

    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggest, AutoSuggestFromHistory, Suggestion, ThreadedAutoSuggest
    from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.lexers import Lexer
    from prompt_toolkit.styles import Style
    from prompt_toolkit.shortcuts import CompleteStyle
except ImportError as _e:  # Sicherheitsnetz: niemals ohne Shell dastehen
    sys.stderr.write(f"hamster: {_e} – starte stattdessen bash. "
                     "(Lösung: sudo apt install python3-prompt-toolkit)\n")
    _fallback_to_bash([], LOGIN)

HOME = os.path.expanduser("~")
HISTFILE = os.path.join(HOME, ".hamster_history")
RCFILE = os.path.join(HOME, ".hamsterrc")

# ───────────────────────────── Farben ─────────────────────────────
R, G, Y, B, M, C, GR = "\033[31m", "\033[32m", "\033[33m", "\033[34m", "\033[35m", "\033[36m", "\033[90m"
BOLD, RST = "\033[1m", "\033[0m"

BASH_BUILTINS = set("""
alias bg bind break builtin caller cd command compgen complete compopt continue declare dirs
disown echo enable eval exec exit export false fc fg getopts hash help history jobs kill let
local logout mapfile popd printf pushd pwd read readarray readonly return set shift shopt
source suspend test times trap true type typeset ulimit umask unalias unset wait . : [
""".split())
# Vollbild-/REPL-Programme: deren stderr wird nicht mitgelesen, sie bekommen das Terminal 1:1.
# (Alle anderen bekommen stderr über ein Pseudo-Terminal → Farben & Fortschrittsbalken bleiben.)
INTERACTIVE = set("vim vi nvim view vimdiff nano pico micro emacs joe mcedit less more most man info "
                  "top htop btop atop nmon iotop glances nvtop mc ranger nnn lf yazi tmux screen zellij byobu "
                  "ssh mosh telnet ftp sftp su login python python2 python3 ipython bpython node irb ghci lua "
                  "bash zsh fish sh dash ksh mysql mariadb psql sqlite3 redis-cli mongo mongosh fzf "
                  "whiptail dialog nmtui alsamixer cfdisk fdisk parted gdisk ncdu tig lazygit lazydocker "
                  "k9s watch minicom screen cmus mutt neomutt w3m lynx newsboat weechat irssi gdb".split())
REPL_WITH_SCRIPT = {"python", "python2", "python3", "node", "bash", "sh", "zsh", "dash", "lua", "irb"}

SUBCOMMANDS = {
    "git": "add bisect branch checkout cherry-pick clone commit diff fetch grep init log merge mv pull push "
           "rebase remote reset restore revert rm show stash status switch tag".split(),
    "apt": "install remove purge update upgrade full-upgrade search show list autoremove".split(),
    "docker": "build compose exec images inspect kill logs ps pull push restart rm rmi run start stop volume network".split(),
    "systemctl": "start stop restart reload status enable disable is-active is-enabled daemon-reload list-units".split(),
    "pip": "install uninstall list show freeze download wheel".split(),
    "npm": "install uninstall run start test init update publish ci audit".split(),
    "cargo": "build run test check new init add clippy fmt doc publish".split(),
    "journalctl": "-u -f -b -e -xe --since".split(),
}
SIGNALS = {2: "SIGINT – mit Strg+C abgebrochen", 9: "SIGKILL – hart beendet (oft: Arbeitsspeicher voll / OOM-Killer)",
           11: "SIGSEGV – Speicherzugriffsfehler (Segfault) im Programm", 13: "SIGPIPE – Ausgabe-Pipe wurde geschlossen",
           15: "SIGTERM – zum Beenden aufgefordert", 6: "SIGABRT – Programm hat sich selbst abgebrochen",
           8: "SIGFPE – Rechenfehler (z. B. Division durch 0)", 4: "SIGILL – ungültige CPU-Anweisung",
           7: "SIGBUS – Busfehler (Speicherzugriff)", 1: "SIGHUP – Terminal geschlossen"}


KEYWORDS = set("if then else elif fi for while until do done case esac in select function time coproc "
               "{ } ! [[ ]]".split())
# Befehle, hinter denen wieder ein Befehl steht (sudo apt ..., timeout 5 ping ...)
CMD_AFTER = {"do", "then", "else", "elif", "while", "until", "if", "!", "{", "time", "coproc",
             "sudo", "doas", "pkexec", "runuser", "nohup", "exec", "command", "builtin", "nice", "ionice",
             "watch", "env", "xargs", "stdbuf", "timeout", "chroot", "setsid", "unbuffer", "torsocks",
             "proxychains", "proxychains4", "strace", "ltrace", "valgrind", "flock", "taskset", "chrt",
             "fakeroot", "eatmydata", "firejail", "nocache", "catchsegv", "systemd-run", "dbus-run-session"}
WRAPPER_ARG_OPTS = {   # Optionen mit Wert dahinter (sudo -u root ...)
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R", "--user", "--group"},
    "doas": {"-u", "-C"}, "pkexec": {"--user", "-u"}, "runuser": {"-u", "-g", "-G"},
    "nice": {"-n", "--adjustment"}, "ionice": {"-c", "-n", "-p"}, "env": {"-u", "-C", "-S", "--unset", "--chdir"},
    "xargs": {"-I", "-n", "-P", "-d", "-a", "-E", "-L", "-s", "--max-args", "--max-procs", "--delimiter"},
    "watch": {"-n", "--interval", "-d"}, "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"}, "strace": {"-e", "-o", "-p", "-s", "-u"},
    "ltrace": {"-e", "-o", "-p", "-s", "-u"}, "flock": {"-w", "-E", "--timeout"}, "time": {"-f", "-o"},
    "systemd-run": {"-p", "-u", "--unit", "--property", "-E", "--setenv"},
}
WRAPPER_POSARGS = {"timeout": 1, "chroot": 1, "flock": 1, "taskset": 1, "chrt": 1}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
SEP_OPS = [";;&", ";;", ";&", "&&", "||", "|&", "|", ";", "&"]
REDIR_RE = re.compile(r"(?:\d+|\{[A-Za-z_]\w*\})?(?:<<<|<<-|<<|<>|>>|>\||>&|<&|>|<)|&>>|&>")
DUP_TARGET_RE = re.compile(r"\d+-?|-")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
# kept for compatibility with older code paths
OPS = SEP_OPS + ["(", ")"]
CMD_OPS = {"&&", "||", "|", "|&", ";", "&", "(", "\n", ";;", ";&", ";;&", "<(", ">("}
REDIR_OPS = set()


# ─────────────────────────── Tokenizer ────────────────────────────
@dataclass
class Tok:
    text: str
    start: int
    end: int
    kind: str           # word | op | redir | comment | assign | funcdef | arith | heredoc | fnparen
    cmdpos: bool = False
    unclosed: int = -1  # Position eines nicht geschlossenen Anführungszeichens / einer Klammer
    role: str = ""      # keyword | cmd | subcmd | option | arg | redir_target | pattern | dbr
    needs_target: bool = False
    cmd: str = ""       # zu welchem Befehl gehört das Wort


class TokList(list):
    open_blocks = 0
    heredoc_open = None


def _skip_dq(line, i):
    """i zeigt auf ", gibt Position nach dem schließenden " zurück (oder -1)."""
    n, j = len(line), i + 1
    while j < n:
        c = line[j]
        if c == "\\":
            j += 2
        elif c == '"':
            return j + 1
        elif c == "$" and j + 1 < n and line[j + 1] in "({":
            k = _scan_balanced(line, j + 1)
            if k < 0:
                return -1
            j = k
        elif c == "`":
            k = _skip_bq(line, j)
            if k < 0:
                return -1
            j = k
        else:
            j += 1
    return -1


def _skip_bq(line, i):
    j = i + 1
    while j < len(line):
        if line[j] == "\\":
            j += 2
        elif line[j] == "`":
            return j + 1
        else:
            j += 1
    return -1


def _scan_balanced(line, i):
    """i zeigt auf ( oder {; gibt Position nach der passenden Klammer zurück (oder -1)."""
    open_ch = line[i]
    close_ch = ")" if open_ch == "(" else "}"
    depth, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                return -1
            i = j + 1
            continue
        if c == '"':
            i = _skip_dq(line, i)
            if i < 0:
                return -1
            continue
        if c == "`":
            i = _skip_bq(line, i)
            if i < 0:
                return -1
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _scan_word(line, i):
    """Liest ein Wort ab i. Gibt (Ende, Position eines offenen Anführungszeichens oder -1) zurück."""
    start, n = i, len(line)
    while i < n:
        c = line[i]
        if c in " \t\n|&;<>":
            break
        if c == "(":
            prev = line[start:i]
            # Array-Zuweisung a=(1 2) oder extglob @(a|b) gehört zum Wort
            if prev and (re.fullmatch(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=", prev) or prev[-1] in "@!+*?"):
                j = _scan_balanced(line, i)
                if j < 0:
                    return n, i
                i = j
                continue
            break
        if c == ")":
            break
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                return n, i
            i = j + 1
            continue
        if c == '"':
            j = _skip_dq(line, i)
            if j < 0:
                return n, i
            i = j
            continue
        if c == "`":
            j = _skip_bq(line, i)
            if j < 0:
                return n, i
            i = j
            continue
        if c == "$" and i + 1 < n and line[i + 1] in "({":
            j = _scan_balanced(line, i + 1)
            if j < 0:
                return n, i
            i = j
            continue
        i += 1
    return min(i, n), -1


def _unquote(word):
    try:
        parts = shlex.split(word)
        return parts[0] if len(parts) == 1 else word
    except ValueError:
        return word


def tokenize(line):
    toks, i, n = TokList(), 0, len(line)
    pending_heredocs = []
    while i < n:
        c = line[i]
        if c in " \t":
            i += 1
            continue
        if c == "\\" and line[i + 1:i + 2] == "\n":      # Zeilenfortsetzung: bash entfernt \ + Zeilenumbruch
            i += 2
            continue
        if c == "\n":
            toks.append(Tok("\n", i, i + 1, "op"))
            i += 1
            for delim, strip_tabs in pending_heredocs:   # Inhalt eines Here-Documents überspringen
                start = i
                while True:
                    j = line.find("\n", i)
                    row = line[i:j if j >= 0 else n]
                    if (row.lstrip("\t") if strip_tabs else row) == delim:
                        i = j + 1 if j >= 0 else n
                        toks.append(Tok(line[start:i], start, i, "heredoc"))
                        break
                    if j < 0:
                        toks.append(Tok(line[start:n], start, n, "heredoc"))
                        toks.heredoc_open = delim
                        i = n
                        break
                    i = j + 1
            pending_heredocs = []
            continue
        if c == "#" and (i == 0 or line[i - 1] in " \t\n;|&()"):
            j = line.find("\n", i)
            j = n if j < 0 else j
            toks.append(Tok(line[i:j], i, j, "comment"))
            i = j
            continue
        if line.startswith(("<(", ">("), i):            # Prozess-Substitution
            toks.append(Tok(line[i:i + 2], i, i + 2, "op"))
            i += 2
            continue
        if line.startswith("((", i):                     # Arithmetik (( ... ))
            j = _scan_balanced(line, i)
            t = Tok(line[i:j if j > 0 else n], i, j if j > 0 else n, "arith", unclosed=-1 if j > 0 else i)
            toks.append(t)
            i = j if j > 0 else n
            continue
        m = REDIR_RE.match(line, i)
        if m:
            j, done = m.end(), False
            if m.group(0).endswith((">&", "<&")):
                m2 = DUP_TARGET_RE.match(line, j)
                if m2:                                  # 2>&1, >&2, <&0, >&-
                    j, done = m2.end(), True
            t = Tok(line[i:j], i, j, "redir", needs_target=not done)
            toks.append(t)
            i = j
            continue
        op = next((o for o in SEP_OPS + ["(", ")"] if line.startswith(o, i)), None)
        if op:
            toks.append(Tok(op, i, i + len(op), "op"))
            i += len(op)
            continue
        end, unc = _scan_word(line, i)
        if end == i:
            end = i + 1
        toks.append(Tok(line[i:end], i, end, "word", unclosed=unc))
        if len(toks) > 1 and toks[-2].kind == "redir" and toks[-2].text.endswith(("<<", "<<-")) \
                and not toks[-2].text.endswith("<<<"):
            pending_heredocs.append((_unquote(toks[-1].text), toks[-2].text.endswith("-")))
        i = end
    if pending_heredocs and toks.heredoc_open is None:
        toks.heredoc_open = pending_heredocs[0][0]
    _analyze(toks)
    return toks


def _analyze(toks):
    """Bestimmt für jedes Wort seine Rolle: Befehl, Schlüsselwort, Option, Argument, Ziel einer Umleitung ..."""
    # name() { ... } erkennen
    for k in range(len(toks) - 2):
        if toks[k].kind == "word" and toks[k + 1].kind == "op" and toks[k + 1].text == "(" \
                and toks[k + 2].text == ")":
            if k == 0 or toks[k - 1].kind == "op" or toks[k - 1].text in ("function", "{", "then", "do", "else"):
                toks[k].kind = "funcdef"
                toks[k + 1].kind = toks[k + 2].kind = "fnparen"
    expect, stack = True, []
    skip_list = fn_next = skip_optarg = False
    wrapper, wrapper_skip = None, 0
    cur_cmd, nargs, prev_word = "", 0, ""
    for idx, t in enumerate(toks):
        top = stack[-1] if stack else None
        if t.kind in ("comment", "heredoc"):
            continue
        if top and top["t"] == "dbr":                       # innerhalb von [[ ... ]]
            if t.kind == "word" and t.text == "]]":
                stack.pop()
                t.role, expect = "keyword", False
            elif t.kind == "word":
                t.role = "dbr"
            continue
        if top and top["t"] == "case" and top["stage"] != "body":
            if t.kind == "word":
                if top["stage"] == "word":
                    t.role, top["stage"] = "arg", "in"
                elif top["stage"] == "in":
                    t.role, top["stage"] = "keyword", "pat"
                elif t.text == "esac":
                    stack.pop()
                    t.role, t.cmdpos, expect = "keyword", True, False
                else:
                    t.role = "pattern"
            elif t.kind == "op" and t.text == ")" and top["stage"] == "pat":
                top["stage"], expect, wrapper = "body", True, None
            continue
        if t.kind == "fnparen":
            expect = True
            continue
        if t.kind == "op":
            o = t.text
            if o in (";;", ";&", ";;&") and top and top["t"] == "case":
                top["stage"], expect = "pat", False
            elif o in ("(", "<(", ">("):
                stack.append({"t": "paren"})
                expect = True
            elif o == ")":
                if top and top["t"] == "paren":
                    stack.pop()
                expect = False
            else:
                expect = True
            wrapper, skip_list, skip_optarg, wrapper_skip = None, False, False, 0
            continue
        if t.kind == "redir":
            continue
        if t.kind == "arith":
            if expect and not skip_list:
                t.role, expect = "arith", False
            continue
        if idx > 0 and toks[idx - 1].kind == "redir" and toks[idx - 1].needs_target:
            t.role = "redir_target"
            continue
        if skip_list:                                       # for x in a b c
            t.role = "keyword" if t.text in ("in", "do") else "arg"
            if t.text == "do":
                skip_list, expect = False, True
            continue
        if fn_next:
            t.kind, fn_next, expect = "funcdef", False, True
            continue
        if not expect:
            t.cmd = cur_cmd
            if cur_cmd == "find" and prev_word in FIND_EXEC:
                t.cmdpos, t.role, expect, cur_cmd = True, "cmd", False, os.path.basename(t.text.lstrip("\\"))
                prev_word = t.text
                continue
            if t.text.startswith("-") and len(t.text) > 1:
                t.role = "option"
            else:
                t.role = "subcmd" if nargs == 0 and cur_cmd in SUBCOMMANDS else "arg"
                nargs += 1
            prev_word = t.text
            continue
        # Befehlsposition
        if ASSIGN_RE.match(t.text):
            t.kind = "assign"
            continue
        if wrapper:
            if t.text.startswith("-") and t.text != "-":
                if t.text in WRAPPER_ARG_OPTS.get(wrapper, ()):
                    skip_optarg = True
                t.role = "option"
                continue
            if skip_optarg:
                skip_optarg, t.role = False, "arg"
                continue
            if wrapper_skip:
                wrapper_skip -= 1
                t.role = "arg"
                continue
        t.cmdpos = True
        w = t.text
        if w in KEYWORDS:
            t.role = "keyword"
            if w in ("if", "while", "until"):
                stack.append({"t": "if" if w == "if" else "loop"})
            elif w in ("for", "select"):
                stack.append({"t": "loop"})
                skip_list, expect = True, False
                continue
            elif w == "case":
                stack.append({"t": "case", "stage": "word"})
                expect = False
                continue
            elif w in ("fi", "done", "}", "esac"):
                want = {"fi": "if", "done": "loop", "}": "brace", "esac": "case"}[w]
                if top and top["t"] == want:
                    stack.pop()
                expect = False
                continue
            elif w == "{":
                stack.append({"t": "brace"})
            elif w == "[[":
                stack.append({"t": "dbr"})
                continue
            elif w == "function":
                fn_next = True
                continue
            if w in ("time",):
                wrapper = "time"
            expect = True
            continue
        base = os.path.basename(w.lstrip("\\"))
        if base in CMD_AFTER:
            t.role, wrapper, wrapper_skip, expect = "cmd", base, WRAPPER_POSARGS.get(base, 0), True
            continue
        t.role, expect, cur_cmd, nargs, wrapper = "cmd", False, base, 0, None
        prev_word = w
    toks.open_blocks = sum(1 for s in stack if s["t"] in ("if", "loop", "case", "brace", "paren"))


# ─────────────────────────── Umgebung ─────────────────────────────
class Env:
    """Merkt sich Aliase und Funktionen der dahinterliegenden bash."""

    EXTRA = {"history", "help", "reload", "jobs", "fg", "bg"}

    def __init__(self):
        self.aliases = {}
        self.functions = set()
        self.last_rc = 0
        self.last_duration = 0.0
        self._cmds = None             # alle Befehle (für Vervollständigung)
        self._path_cache = (None, None)  # (PATH, Programme in PATH)
        self._which = {}              # schneller Einzel-Check, ohne kompletten PATH-Scan
        self._lock = threading.Lock()

    def parse_defs(self, text):
        aliases = {}
        for m in re.finditer(r"^alias ([^=\s]+)='(.*)'$", text, re.M):
            aliases[m.group(1)] = m.group(2).replace("'\\''", "'")
        funcs = set(re.findall(r"^([^\s=()]+) \(\) $", text, re.M))
        if aliases != self.aliases or funcs != self.functions:
            self._cmds = None
        self.aliases, self.functions = aliases, funcs

    def path_commands(self):
        """Alle Programme im PATH. Ergebnis bleibt gespeichert, bis sich PATH ändert."""
        path = os.environ.get("PATH", "")
        key = (path, self._mtimes(path))
        with self._lock:
            if self._path_cache[0] == key:
                return self._path_cache[1]
            found = set()
            for d in path.split(":"):
                try:
                    with os.scandir(d or ".") as it:
                        for e in it:
                            try:
                                if e.is_file():
                                    found.add(e.name)
                            except OSError:
                                pass
                except OSError:
                    pass
            self._path_cache, self._cmds, self._which = (key, found), None, {}
            return found

    @staticmethod
    def _mtimes(path):
        out = []
        for d in path.split(":"):
            try:
                out.append(os.stat(d or ".").st_mtime_ns)
            except OSError:
                out.append(0)
        return tuple(out)

    def warm_up(self):
        """PATH im Hintergrund einlesen, damit die erste Tab-Vervollständigung sofort da ist."""
        threading.Thread(target=self.path_commands, daemon=True).start()

    def commands(self):
        if self._cmds is None or self._path_cache[0] is None or \
                self._path_cache[0] != (os.environ.get("PATH", ""), self._mtimes(os.environ.get("PATH", ""))):
            self.path_commands()
        if self._cmds is None:
            self._cmds = (set(BASH_BUILTINS) | set(self.aliases) | self.functions
                          | self.EXTRA | self._path_cache[1])
        return self._cmds

    def exists(self, word):
        w = os.path.expanduser(word)
        if "$" in w or "`" in w or w.startswith(("(", "'", '"')):
            return True  # dynamisch – nicht prüfbar
        if "/" in w:
            return os.path.isfile(w) and os.access(w, os.X_OK) or os.path.isdir(w)
        if w in KEYWORDS or w in BASH_BUILTINS or w in self.aliases or w in self.functions or w in self.EXTRA:
            return True
        if w == "..":
            return True
        path = os.environ.get("PATH", "")
        if self._path_cache[0] is not None and self._path_cache[0][0] == path:
            return w in self._path_cache[1] or w in self._which.get(path, ()) or self._which_check(w, path)
        return self._which_check(w, path)

    def _which_check(self, w, path):
        known = self._which.setdefault(path, set())
        if w in known:
            return True
        if shutil.which(w, path=path):
            known.add(w)   # nur Treffer merken – neu installierte Programme werden sofort erkannt
            return True
        return False


ENV = Env()


# ─────────────────────── Hervorhebung beim Tippen ─────────────────
_SEG_RE = re.compile(r"'[^']*'?|\"(?:\\.|[^\"\\])*\"?|\$\{[^}]*\}?|\$\([^)]*\)?|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9#?@*$!-]")
GLOB_CHARS = set("*?[")


def _word_frags(text, base):
    """Teilt ein Wort in Abschnitte: 'Strings', $VARIABLEN, Rest."""
    frags, pos = [], 0
    for m in _SEG_RE.finditer(text):
        if m.start() > pos:
            frags.append((base, text[pos:m.start()]))
        frags.append(("class:string" if m.group(0)[0] in "'\"" else "class:var", m.group(0)))
        pos = m.end()
    if pos < len(text):
        frags.append((base, text[pos:]))
    return frags


def _arg_style(t):
    w = t.text
    if t.role == "option":
        return "class:option"
    if t.role == "subcmd":
        return "class:subcmd"
    if t.role in ("pattern", "dbr"):
        return "class:option" if w.startswith("-") else ""
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", w):
        return "class:number"
    plain = _unquote(w) if w[:1] in "'\"" else w
    if "$" not in plain and "`" not in plain:
        p = os.path.expanduser(plain)
        if t.cmd in ("cd", "pushd") and t.role == "arg":
            return "class:path" if os.path.isdir(p) else "class:badpath"
        if any(ch in GLOB_CHARS for ch in w) and w[:1] not in "'\"":
            return "class:glob"
        if len(p) < 4096 and os.path.exists(p):
            return "class:path"
    return ""


class HamsterLexer(Lexer):
    def lex_document(self, document):
        text = document.text
        toks = tokenize(text)
        styled = []   # (start, end, [frags])
        for idx, t in enumerate(toks):
            seg = text[t.start:t.end]
            if t.kind in ("op", "redir", "fnparen"):
                frags = [("class:op", seg)]
            elif t.kind == "comment":
                frags = [("class:comment", seg)]
            elif t.kind == "heredoc":
                frags = [("class:string", seg)]
            elif t.kind == "arith":
                frags = [("class:var", seg)]
            elif t.kind == "funcdef":
                frags = [("class:cmd", seg)]
            elif t.kind == "assign":
                name_end = seg.index("=") + 1
                frags = [("class:var", seg[:name_end])] + _word_frags(seg[name_end:], "")
            elif t.unclosed >= 0:
                cut = t.unclosed - t.start
                frags = [("", seg[:cut]), ("class:error", seg[cut:])]
            elif t.role == "keyword":
                frags = [("class:keyword", seg)]
            elif t.cmdpos:
                frags = [("class:cmd" if ENV.exists(seg.lstrip("\\")) else "class:badcmd", seg)]
            else:
                style = _arg_style(t)
                if t.role == "redir_target" and idx > 0 and toks[idx - 1].text.endswith("<") \
                        and not os.path.exists(os.path.expanduser(_unquote(seg))):
                    style = "class:badpath"
                frags = _word_frags(seg, style) if style in ("", "class:number") else [(style, seg)]
            styled.append((t.start, t.end, frags))
        flat, pos = [], 0
        for start, end, frags in styled:
            if start > pos:
                flat.append(("", text[pos:start]))
            flat.extend(frags)
            pos = end
        if pos < len(text):
            flat.append(("", text[pos:]))
        lines = [[]]
        for style, s in flat:      # auf Zeilen aufteilen (mehrzeilige Eingabe)
            parts = s.split("\n")
            for k, part in enumerate(parts):
                if k:
                    lines.append([])
                if part:
                    lines[-1].append((style, part))
        return lambda i: lines[i] if i < len(lines) else []


# ─────────────────── bash-completion (jedes Wort) ─────────────────
BASH_COMPLETION_FILES = ["/usr/share/bash-completion/bash_completion", "/etc/bash_completion"]
BRIDGE_INIT = r'''
shopt -s extglob progcomp
__hc_flags=
compopt() {   # Optionen merken (filenames, nospace ...) statt sie anzuwenden
  local a; for a; do case $a in filenames|nospace|dirnames|noquote|plusdirs|default|bashdefault) __hc_flags+=" $a";; esac; done
  return 0
}
__hc_spec() { complete -p -- "$1" 2>/dev/null || complete -p -- "${1##*/}" 2>/dev/null; }
__hc_out() { printf '%s\0' "F:$__hc_flags" "${COMPREPLY[@]}"; printf '\0__HC_END__\0'; }
__hc() {
  local cword=$1 line=$2; shift 2
  local -a COMP_WORDS=("$@")
  local COMP_CWORD=$cword COMP_LINE=$line COMP_POINT=${#line} COMP_TYPE=9 COMP_KEY=9
  local cmd=${COMP_WORDS[0]} cur=${COMP_WORDS[cword]} prev=${COMP_WORDS[cword-1]}
  COMPREPLY=(); __hc_flags=
  local spec try func i x; local -a s cg
  for try in 1 2; do
    spec=$(__hc_spec "$cmd")
    if [[ -z $spec ]]; then
      if declare -F _comp_load >/dev/null; then _comp_load -- "$cmd" >/dev/null 2>&1
      elif declare -F __load_completion >/dev/null; then __load_completion "$cmd" >/dev/null 2>&1; fi
      spec=$(__hc_spec "$cmd")
    fi
    [[ -z $spec ]] && break
    eval "s=($spec)"
    func=; cg=(); i=1
    while (( i < ${#s[@]} - 1 )); do
      case ${s[i]} in
        -F) func=${s[i+1]}; ((i+=2));;
        -C) ((i+=2));;
        -o) __hc_flags+=" ${s[i+1]}"; ((i+=2));;
        -[AGWXPS]) cg+=("${s[i]}" "${s[i+1]}"); ((i+=2));;
        -[DEI]) ((i++));;
        -*) cg+=("${s[i]}"); ((i++));;
        *) ((i++));;
      esac
    done
    if [[ -n $func ]]; then
      "$func" "$cmd" "$cur" "$prev" </dev/null >/dev/null 2>&1
      (( $? == 124 && try == 1 )) && { COMPREPLY=(); continue; }
    fi
    break
  done
  if (( ${#cg[@]} )); then
    while IFS= read -r x; do COMPREPLY+=("$x"); done < <(compgen "${cg[@]}" -- "$cur" 2>/dev/null)
  fi
  if (( ${#COMPREPLY[@]} == 0 )) && [[ -z $spec || $__hc_flags == *default* ]]; then
    while IFS= read -r x; do COMPREPLY+=("$x"); done < <(compgen -f -- "$cur" 2>/dev/null)
    __hc_flags+=" filenames"
  fi
  __hc_out
}
'''


class BashCompletion:
    """Fragt eine im Hintergrund laufende bash mit geladenem bash-completion nach Vorschlägen.
    So kennt hamster die Argumente von hunderten Programmen (git, apt, systemctl, docker, ssh, ...)."""

    def __init__(self):
        self.proc, self.lock = None, threading.Lock()
        self.script = next((f for f in BASH_COMPLETION_FILES if os.path.isfile(f)), None)
        self.state = None

    def available(self):
        return self.script is not None and os.environ.get("HAMSTER_NO_BASH_COMPLETION") != "1"

    def _start(self):
        self.proc = subprocess.Popen(["bash", "--norc", "--noprofile"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self.state = None
        self._write(f"source {shlex.quote(self.script)} >/dev/null 2>&1\n{BRIDGE_INIT}\n")

    def _write(self, s):
        self.proc.stdin.write(s.encode())
        self.proc.stdin.flush()

    def _kill(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=1)
        except Exception:
            pass
        self.proc = None

    def warm_up(self):
        if self.available():
            threading.Thread(target=lambda: self.complete(["true", ""], 1, "true "), daemon=True).start()

    def complete(self, words, cword, line, timeout=1.5):
        """Gibt (flags, kandidaten) zurück oder None."""
        if not self.available() or not self.lock.acquire(timeout=timeout):
            return None
        try:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            pre = []
            try:
                defs_mtime = os.stat(DEFS).st_mtime_ns
            except OSError:
                defs_mtime = None
            state = (os.getcwd(), os.environ.get("PATH", ""), defs_mtime)
            if state != self.state:
                pre.append(f"builtin cd -- {shlex.quote(state[0])} 2>/dev/null; export PATH={shlex.quote(state[1])}")
                if defs_mtime and (self.state is None or self.state[2] != defs_mtime):
                    pre.append(f"source {shlex.quote(DEFS)} >/dev/null 2>&1")
                self.state = state
            args = " ".join(shlex.quote(x) for x in [str(cword), line] + list(words))
            self._write("\n".join(pre + [f"__hc {args}"]) + "\n")
            fd, buf, deadline = self.proc.stdout.fileno(), b"", time.monotonic() + timeout
            while not buf.endswith(b"\0__HC_END__\0"):
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([fd], [], [], left)[0]:
                    self._kill()     # hängt (z. B. Netzwerk) → beim nächsten Mal neu starten
                    return None
                chunk = os.read(fd, 65536)
                if not chunk:
                    self._kill()
                    return None
                buf += chunk
            parts = buf[:-len(b"\0__HC_END__\0")].decode(errors="replace").split("\0")
            flags = set(parts[0][2:].split()) if parts and parts[0].startswith("F:") else set()
            seen, cands = set(), []
            for c in parts[1:]:
                if c and c not in seen:
                    seen.add(c)
                    cands.append(c)
            return flags, cands
        except (OSError, ValueError, BrokenPipeError):
            self._kill()
            return None
        finally:
            self.lock.release()


BASHCOMP = BashCompletion()
_SPECIAL = re.compile(r"([ \t'\"()&;|<>$`!\\{}])")


# ───────────────────────── Vervollständigung ──────────────────────
def path_completions(word, only_dirs=False):
    expanded = os.path.expanduser(word)
    d, prefix = os.path.split(expanded)
    try:
        entries = sorted(os.listdir(d or "."))
    except OSError:
        return
    if not prefix.startswith("."):
        entries = [e for e in entries if not e.startswith(".")]
    matches = [e for e in entries if e.startswith(prefix)]
    if not matches:  # unscharf: Groß/Klein egal, oder Teilstring
        matches = [e for e in entries if prefix.lower() in e.lower()]
    for e in matches:
        full = os.path.join(d, e)
        is_dir = os.path.isdir(full)
        if only_dirs and not is_dir:
            continue
        shown = os.path.join(os.path.split(word)[0], e) + ("/" if is_dir else "")
        if any(ch in shown for ch in " '\"()&;|$"):
            shown = shlex.quote(shown)
        yield shown, ("Ordner" if is_dir else "Datei")


def _current_command(text):
    """Wörter des Befehls, in dem der Cursor steht (ab dem Programmnamen), und ob ein neues Wort beginnt."""
    new_word = not text or text[-1] in " \t\n"
    toks = tokenize(text + ("x" if new_word else ""))
    seg = []
    for t in toks:
        if t.kind == "op" or t.role == "keyword" and t.cmdpos:
            seg = []
        elif t.kind in ("word", "funcdef") and t.role != "redir_target":
            if not seg and not t.cmdpos:
                continue
            seg.append(t)
        elif t.kind == "assign" and not seg:
            continue
    last = toks[-1] if toks else None
    return seg, last, new_word


class HamsterCompleter(Completer):
    def __init__(self, use_bash=True):
        self.use_bash = use_bash

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        seg, last, new_word = _current_command(text)
        word = "" if new_word else (last.text if last else "")
        if last is not None and last.kind not in ("word", "assign", "funcdef") and not new_word:
            return
        if word.startswith("$") and "/" not in word:
            for k in sorted(os.environ):
                if k.startswith(word[1:].lstrip("{")):
                    yield Completion(("${" if word.startswith("${") else "$") + k + ("}" if word.startswith("${") else ""),
                                     -len(word), display_meta=os.environ[k][:40])
            return
        cmdpos = last is not None and last.cmdpos and last.role != "keyword" or \
            (last is not None and last.role == "keyword" and last.cmdpos and not new_word)
        if cmdpos and "/" not in word:
            matches = sorted(c for c in ENV.commands() | KEYWORDS if c.startswith(word))
            if not matches and word:
                matches = difflib.get_close_matches(word, ENV.commands(), n=8, cutoff=0.6)
            for c in matches[:300]:
                meta = "Alias: " + ENV.aliases[c] if c in ENV.aliases else \
                    "Funktion" if c in ENV.functions else "eingebaut" if c in BASH_BUILTINS else \
                    "Schlüsselwort" if c in KEYWORDS else ""
                yield Completion(c, -len(word), display_meta=meta)
            return
        # Argument: zuerst bash-completion fragen
        if self.use_bash and seg and last is not None and last.role != "redir_target":
            words = [t.text for t in seg[:-1]] + [word] if not new_word else [t.text for t in seg[:-1]] + [""]
            line = text[seg[0].start:] if seg else text
            res = BASHCOMP.complete(words, len(words) - 1, line)
            if res and res[1]:
                flags, cands = res
                yield from self._from_bash(word, cands, flags)
                return
        cmd = os.path.basename(seg[0].text) if seg else ""
        args = seg[1:-1] if seg else []
        if cmd in SUBCOMMANDS and not args:
            for s in SUBCOMMANDS[cmd]:
                if s.startswith(word):
                    yield Completion(s, -len(word), display_meta=cmd + " Unterbefehl")
        for shown, meta in path_completions(word, cmd in ("cd", "pushd", "rmdir")):
            yield Completion(shown, -len(word), display_meta=meta)

    @staticmethod
    def _from_bash(word, cands, flags):
        is_files = "filenames" in flags or "dirnames" in flags
        # Bei --opt=wert und host:pfad liefert bash-completion nur den Teil nach = bzw. :
        replace = word
        if cands and not any(c.startswith(word) for c in cands):
            for sep in ("=", ":"):
                if sep in word:
                    replace = word.rsplit(sep, 1)[1]
                    break
        quote = replace[:1] if replace[:1] in "'\"" else ""
        single = len(cands) == 1
        for c in sorted(cands, key=lambda x: (x.startswith("-"), x.lower())) if len(cands) < 2000 else cands[:2000]:
            meta = ""
            out = c
            if is_files:
                full = os.path.expanduser(c)
                if os.path.isdir(full):
                    out, meta = (c if c.endswith("/") else c + "/"), "Ordner"
                elif os.path.exists(full):
                    meta = "Datei"
                if quote:
                    out = quote + out + ("" if out.endswith("/") else quote)
                elif "noquote" not in flags:
                    out = _SPECIAL.sub(r"\\\1", out)
            elif c.startswith("-"):
                meta = "Option"
            if single and not out.endswith(("/", "=")) and "nospace" not in flags:
                out += " "
            yield Completion(out, -len(replace), display=c, display_meta=meta)


class HamsterSuggest(AutoSuggest):
    """Erst aus dem Verlauf, sonst die eindeutige Vervollständigung (wie fish)."""
    hist = AutoSuggestFromHistory()
    comp = HamsterCompleter(use_bash=False)   # beim Tippen nur schnelle Quellen

    def get_suggestion(self, buffer, document):
        s = self.hist.get_suggestion(buffer, document)
        if s or not document.text or document.text[-1] in " \n":
            return s
        try:
            comps = list(self.comp.get_completions(document, None))
        except Exception:
            return None
        if len(comps) == 1:
            c = comps[0]
            word = document.text[len(document.text) + c.start_position:]
            if c.text.startswith(word) and c.text != word:
                return Suggestion(c.text[len(word):])
        return None


# ───────────────────────── Fehleranzeige ──────────────────────────
def box(title, line=None, start=0, end=0, lines=()):
    print(f"\n{R}{BOLD}✗ {title}{RST}", file=sys.stderr)
    if line is not None:
        print(f"{R}│{RST} {line}", file=sys.stderr)
        print(f"{R}│{RST} {' ' * start}{R}{BOLD}{'^' * max(1, end - start)}{RST}", file=sys.stderr)
    for l in lines:
        print(f"{R}│{RST} {l}", file=sys.stderr)
    print(file=sys.stderr)


def show_source(path, lineno, col=None, ctx=2):
    """Zeigt die betroffene Code-Zeile mit Umgebung und markiert die Spalte."""
    try:
        src = open(path, errors="replace").read().splitlines()
    except OSError:
        return []
    out = []
    for i in range(max(1, lineno - ctx), min(len(src), lineno + ctx) + 1):
        mark = f"{R}{BOLD}→{RST}" if i == lineno else " "
        text = src[i - 1].expandtabs(4)
        out.append(f"{mark} {GR}{i:>4} │{RST} " + (f"{BOLD}{text}{RST}" if i == lineno else text))
        if i == lineno and col:
            out.append(f"  {GR}     │{RST} " + " " * (col - 1) + f"{R}{BOLD}^ hier{RST}")
    return out


def suggest_names(word, pool, n=3):
    pool = list(pool)
    # Buchstabendreher (gti → git) zuerst
    swapped = [p for p in pool if p != word and sorted(p) == sorted(word)]
    m = swapped + [x for x in difflib.get_close_matches(word, pool, n=n, cutoff=0.55) if x not in swapped]
    m = m[:n]
    return f"Meintest du: {', '.join(f'{G}{BOLD}{x}{RST}' for x in m)} ?" if m else None


def precheck(line):
    """Prüft vor dem Ausführen. Gibt True zurück, wenn alles ok ist."""
    toks = tokenize(line)
    for t in toks:
        if t.unclosed >= 0:
            q = line[t.unclosed]
            what = "Klammer $( )" if q == "$" else f"Anführungszeichen {q}"
            box(f"Syntaxfehler: {what} wird nicht geschlossen", line, t.unclosed, t.unclosed + 1,
                [f"Hier geöffnet, aber nirgends wieder zu. Füge am Ende ein {q if q != '$' else ')'} hinzu."])
            return False
    for t in toks:
        if t.kind == "arith" and t.unclosed >= 0:
            box("Syntaxfehler: (( wird nicht mit )) geschlossen", line, t.start, t.start + 2)
            return False
    if toks.heredoc_open is not None:
        box(f"Here-Document wird nicht beendet", line, 0, len(line.split(chr(10))[0]),
            [f"Es fehlt eine Zeile, die nur {BOLD}{toks.heredoc_open}{RST} enthält."])
        return False
    real = [t for t in toks if t.kind not in ("comment", "heredoc")]
    for i, t in enumerate(real):
        nxt = real[i + 1] if i + 1 < len(real) else None
        prev = real[i - 1] if i > 0 else None
        if t.kind == "redir" and t.needs_target and (nxt is None or (nxt.kind == "op" and nxt.text not in ("<(", ">("))):
            box(f"Syntaxfehler: nach '{t.text}' fehlt ein Dateiname", line, t.start, t.end,
                ["Beispiel: ls > liste.txt"])
            return False
        if t.kind == "op" and t.text in ("|", "&&", "||", "|&"):
            if prev is None or (prev.kind == "op" and prev.text != ")"):
                box(f"Syntaxfehler: vor '{t.text}' steht kein Befehl", line, t.start, t.end)
                return False
            if nxt is None:
                box(f"Syntaxfehler: nach '{t.text}' fehlt ein Befehl", line, t.start, t.end,
                    ["Die Zeile endet mit einem Operator – da muss noch etwas kommen."])
                return False
    defined_here = {t.text for t in toks if t.kind == "funcdef"}
    for t in toks:
        if t.cmdpos and t.role != "keyword" and t.text not in defined_here and not ENV.exists(t.text.lstrip("\\")):
            word = t.text.lstrip("\\")
            lines = []
            if "/" in word:
                p = os.path.expanduser(word)
                if not os.path.exists(p):
                    lines.append(f"Die Datei {BOLD}{word}{RST} existiert nicht.")
                    d, f = os.path.split(p)
                    try:
                        s = suggest_names(f, os.listdir(d or "."))
                        if s:
                            lines.append(s)
                    except OSError:
                        lines.append(f"Auch der Ordner {BOLD}{d}{RST} existiert nicht.")
                elif not os.access(p, os.X_OK):
                    lines += [f"{word} ist nicht ausführbar.",
                              f"Lösung: {G}chmod +x {word}{RST}  oder  {G}bash {word}{RST}"]
                box("Nicht ausführbar" if os.path.exists(p) else "Datei nicht gefunden", line, t.start, t.end, lines)
            else:
                lines.append(f"Den Befehl {BOLD}{word}{RST} gibt es nicht.")
                s = suggest_names(word, ENV.commands())
                if s:
                    lines.append(s)
                if os.path.isfile(word):
                    lines.append(f"Im aktuellen Ordner liegt eine Datei '{word}' → starte sie mit {G}./{word}{RST}")
                pkg = package_hint(word)
                if pkg:
                    lines.append(pkg)
                box("Befehl nicht gefunden", line, t.start, t.end, lines)
            return False
    return True


def package_hint(word):
    cnf = "/usr/lib/command-not-found"
    if not os.path.exists(cnf):
        return None
    try:
        out = subprocess.run([cnf, "--", word], capture_output=True, text=True, timeout=3).stderr
        m = re.search(r"(sudo )?apt install \S+", out)
        return f"Installieren mit: {G}{m.group(0)}{RST}" if m else None
    except Exception:
        return None


# Muster für Fehlermeldungen in der Ausgabe → Erklärung
def explain_stderr(err, line):
    lines = []
    text = "\n".join(err[-60:])

    # Python-Traceback: letzte Stelle in einer echten Datei
    frames = re.findall(r'File "([^"<]+)", line (\d+)', text)
    if frames:
        f, ln = frames[-1]
        user_frames = [fr for fr in frames if "/lib/python" not in fr[0] and "site-packages" not in fr[0]]
        if user_frames:
            f, ln = user_frames[-1]
        exc = next((l for l in reversed(err) if re.match(r"^\w+(\.\w+)*(Error|Exception|Interrupt|Exit)\b", l)), "")
        lines.append(f"Python-Fehler in {BOLD}{f}{RST}, Zeile {BOLD}{ln}{RST}:")
        if exc:
            lines.append(f"{Y}{exc}{RST}")
        lines += show_source(f, int(ln))
        mm = re.search(r"No module named '([\w.]+)'", exc)
        if mm:
            mod = mm.group(1).split(".")[0]
            lines.append(f"Modul fehlt. Installieren: {G}sudo apt install python3-{mod.lower().replace('_', '-')}{RST}"
                         f"  oder in einer venv: {G}pip install {mod}{RST}")
        return lines

    # git: Tippfehler im Unterbefehl
    m = re.search(r"git: '([^']+)' is not a git command", text)
    if m:
        similar = re.findall(r"^\s+([\w-]+)$", text, re.M)
        lines = [f"Den git-Befehl {BOLD}{m.group(1)}{RST} gibt es nicht."]
        if similar:
            lines.append(f"Meintest du: {', '.join(G + BOLD + x + RST for x in similar[:3])} ?")
        i = line.find(m.group(1))
        if i >= 0:
            box("Unbekannter git-Befehl", line, i, i + len(m.group(1)), lines)
            return None
        return lines

    # Compiler / Linter: datei:zeile:spalte: error: ...
    m = re.search(r"^([^\s:]+):(\d+):(?:(\d+):)?\s*(fatal error|error|Fehler)\s*:?\s*(.*)$", text, re.M)
    if m:
        f, ln, col, _, msg = m.groups()
        lines.append(f"Fehler in {BOLD}{f}{RST}, Zeile {BOLD}{ln}{RST}" + (f", Spalte {BOLD}{col}{RST}" if col else "") + ":")
        lines.append(f"{Y}{msg}{RST}")
        lines += show_source(f, int(ln), int(col) if col else None)
        return lines

    # Shell-Skript: ./x.sh: line 3: ...
    m = re.search(r"^([^\s:]+): (?:line|Zeile) (\d+): (.*)$", text, re.M)
    if m and os.path.isfile(m.group(1)):
        lines.append(f"Fehler im Skript {BOLD}{m.group(1)}{RST}, Zeile {BOLD}{m.group(2)}{RST}:")
        lines.append(f"{Y}{m.group(3)}{RST}")
        lines += show_source(m.group(1), int(m.group(2)))
        return lines

    m = re.search(r"['‘\"]?([^'’\":\n]+)['’\"]?: (No such file or directory|Datei oder Verzeichnis nicht gefunden)", text)
    if m:
        missing = m.group(1).strip()
        missing = re.sub(r"^.*cannot access\s*|^.*kann nicht auf\s*", "", missing).strip("'‘’\" ")
        lines.append(f"{BOLD}{missing}{RST} existiert nicht.")
        d, f = os.path.split(os.path.expanduser(missing))
        try:
            s = suggest_names(f, os.listdir(d or "."))
            if s:
                lines.append(s)
        except OSError:
            if d:
                lines.append(f"Schon der Ordner {BOLD}{d}{RST} fehlt.")
        if missing in line:
            i = line.find(missing)
            box("Datei oder Ordner nicht gefunden", line, i, i + len(missing), lines)
            return None
        return lines

    cmd0 = (line.split() or [""])[0]
    rules = [
        (r"externally-managed-environment",
         ["pip darf auf Ubuntu/Debian nicht ins System installieren (Schutz der Systempakete).",
          f"Lösung 1: {G}sudo apt install python3-PAKET{RST}",
          f"Lösung 2: virtuelle Umgebung: {G}python3 -m venv .venv && source .venv/bin/activate{RST}, dann pip install",
          f"Lösung 3 (für Programme): {G}pipx install PAKET{RST}"]),
        (r"Permission denied \(publickey",
         ["Der Server akzeptiert deinen SSH-Schlüssel nicht.",
          f"Schlüssel anlegen: {G}ssh-keygen -t ed25519{RST}, dann den Inhalt von ~/.ssh/id_ed25519.pub beim Server "
          f"(ssh-copy-id) bzw. bei GitHub/GitLab eintragen."]),
        (r"is not in the sudoers file|ist nicht in der sudoers",
         ["Dein Benutzer darf kein sudo benutzen.", f"Ein Admin muss ihn freischalten: {G}usermod -aG sudo NAME{RST}"]),
        (r"Could not get lock|is held by process|Unable to acquire the dpkg frontend lock.*held",
         ["Ein anderes apt/dpkg läuft gerade (z. B. automatische Updates).",
          "Warte ein bis zwei Minuten und versuch es dann nochmal."]),
        (r"dpkg was interrupted|dpkg wurde unterbrochen",
         ["Eine frühere Installation wurde abgebrochen.", f"Reparieren: {G}sudo dpkg --configure -a{RST}"]),
        (r"\(fetch first\)|non-fast-forward|Updates were rejected",
         ["Auf dem Server gibt es neuere Commits als bei dir.",
          f"Erst holen: {G}git pull --rebase{RST}, dann nochmal {G}git push{RST}."]),
        (r"would be overwritten by (merge|checkout)|Your local changes",
         ["Deine ungespeicherten Änderungen sind im Weg.",
          f"Zur Seite legen: {G}git stash{RST} (später zurück mit {G}git stash pop{RST}) – oder vorher committen."]),
        (r"CONFLICT \(", ["Merge-Konflikt. Öffne die genannten Dateien, löse die <<<<<<< / >>>>>>> Stellen,",
                          f"dann {G}git add DATEI{RST} und {G}git commit{RST} (oder {G}git merge --abort{RST})."]),
        (r"permission denied while trying to connect to the Docker daemon",
         ["Dein Benutzer darf Docker nicht benutzen.",
          f"Lösung: {G}sudo usermod -aG docker $USER{RST}, danach ab- und wieder anmelden."]),
        (r"Cannot connect to the Docker daemon", ["Docker läuft nicht.", f"Starten: {G}sudo systemctl start docker{RST}"]),
        (r"Unit \S+ (not found|could not be found)",
         ["Diesen Dienst gibt es nicht.", f"Alle Dienste: {G}systemctl list-units --type=service --all{RST}"]),
        (r"Interactive authentication required|Access denied",
         [f"Dafür braucht es Admin-Rechte → {G}sudo {line}{RST}"]),
        (r"Permission denied|Keine Berechtigung|Operation not permitted|Vorgang nicht zulässig",
         ["Keine Berechtigung.", f"Versuch es mit {G}sudo {line}{RST} oder prüfe die Rechte mit {G}ls -l{RST}."]),
        (r"[Ii]s a directory|Ist ein Verzeichnis", ["Das ist ein Ordner, keine Datei. Für Ordner z. B. ls oder cd benutzen."]),
        (r"Not a directory|Ist kein Verzeichnis", ["Das ist eine Datei, kein Ordner."]),
        (r"not a git repository", ["Du bist in keinem git-Projekt.", f"Neues anlegen: {G}git init{RST} – oder erst mit cd in den Projektordner wechseln."]),
        (r"No space left on device", ["Die Festplatte ist voll.", f"Platz prüfen: {G}df -h{RST}  Große Ordner finden: {G}du -sh * | sort -h{RST}"]),
        (r"Could not resolve host|Temporary failure in name resolution|Name or service not known",
         ["Der Servername konnte nicht aufgelöst werden – Internet/DNS prüfen oder Tippfehler im Namen?"]),
        (r"Connection refused", ["Verbindung abgelehnt – läuft der Dienst/Server auf diesem Port?"]),
        (r"Connection timed out|timed out", ["Zeitüberschreitung – Server nicht erreichbar (Firewall/Netzwerk?)."]),
        (r"Address already in use", ["Der Port ist schon belegt.", f"Wer benutzt ihn: {G}ss -ltnp{RST}"]),
        (r"Unable to locate package|E: Paket .* kann nicht gefunden",
         ["Paket nicht gefunden.", f"Erst {G}sudo apt update{RST}, dann Namen mit {G}apt search NAME{RST} suchen."]),
        (r"Could not open lock file|Unable to acquire the dpkg|are you root",
         [f"apt braucht Admin-Rechte → {G}sudo{RST} davor schreiben."]),
        (r"Read-only file system|Schreibgeschütztes Dateisystem", ["Das Dateisystem ist schreibgeschützt."]),
        (r"(unrecognized|invalid|unknown|ungültige|unbekannte) option", ["Diese Option kennt das Programm nicht.",
         f"Erlaubte Optionen: {G}{cmd0} --help{RST}"]),
        (r"missing operand|fehlender Operand", ["Es fehlt ein Argument (z. B. Datei- oder Ordnername).",
         f"Hilfe: {G}{cmd0} --help{RST}"]),
        (r"Directory not empty|Verzeichnis nicht leer", [f"Ordner ist nicht leer – zum Löschen mit Inhalt: {G}rm -r ORDNER{RST}"]),
        (r"File exists|Die Datei existiert bereits", ["Das Ziel existiert bereits."]),
        (r"command not found|Befehl nicht gefunden", ["Ein Befehl (z. B. in einem Skript) wurde nicht gefunden."]),
        (r"syntax error|Syntaxfehler", ["Syntaxfehler – siehe Meldung oben."]),
    ]
    for pat, expl in rules:
        if re.search(pat, text):
            return expl
    return lines


def explain_exit(rc):
    if rc == 126:
        return "Die Datei ist nicht ausführbar (chmod +x DATEI)."
    if rc == 127:
        return "Befehl nicht gefunden."
    if rc > 128 and rc - 128 in SIGNALS:
        return SIGNALS[rc - 128]
    if rc == 2:
        return "Falsche Benutzung (falsche Argumente?) – siehe --help."
    return None


# ───────────────────────── bash im Hintergrund ────────────────────
# Jeder Befehl läuft in echter bash. Danach werden Ordner, Umgebungsvariablen,
# Aliase, Funktionen und Shell-Variablen gesichert und beim nächsten Befehl
# wieder geladen – so verhält sich alles wie in einer durchgehenden bash-Sitzung.
STATE_DIR = tempfile.mkdtemp(prefix="hamster-")
DEFS = os.path.join(STATE_DIR, "defs")
atexit.register(shutil.rmtree, STATE_DIR, True)
_run_counter = [0]

DUMP = r'''
{ pwd > "$__hs/cwd"; env -0 > "$__hs/env"
  { alias -p
    # Funktionen sichern – außer denen von bash-completion (die lädt hamster selbst)
    shopt -s extdebug
    __fl=$(compgen -A function)
    if [[ -n $__fl ]]; then
      while read -r __n __l __f; do
        case $__f in */bash-completion/*|*/bash_completion*|*/completions/*) continue;; esac
        declare -f "$__n"
      done <<< "$(declare -F $__fl)"
    fi
    shopt -u extdebug
    complete -p
    for __v in $(compgen -v); do
      case $__v in BASH*|SHELLOPTS|EUID|UID|PPID|GROUPS|FUNCNAME|PIPESTATUS|RANDOM|SRANDOM|SECONDS|\
LINENO|HISTCMD|EPOCH*|COMP*|DIRSTACK|_|__*|SHLVL|OLDPWD|PWD|COLUMNS|LINES|OPT*|IFS|PS[0-4]|\
HOSTNAME|HOSTTYPE|MACHTYPE|OSTYPE|HIST*|MAILCHECK|TERM|READLINE*) continue;; esac
      __a=${!__v@a}; [[ $__a == *[rx]* ]] && continue
      declare -p "$__v"
    done
  } > "$__hs/defs"
} 2>/dev/null
'''

SHOPTS = "shopt -s expand_aliases extglob globstar checkwinsize 2>/dev/null"

# Nach jedem Befehl: PROMPT_COMMAND ausführen (direnv, zoxide, Terminal-Titel, ...) – mit dem Exit-Code
AFTER_COMMAND = r'''
if [[ ${HAMSTER_NO_PROMPT_COMMAND:-0} != 1 ]]; then
  __hs_rc() { return "$1"; }
  for __pc in "${PROMPT_COMMAND[@]}"; do __hs_rc "$__hamster_rc"; eval "$__pc"; done 2>/dev/null
  unset -f __hs_rc
fi
'''

DEFAULT_ALIASES = r'''
alias ls >/dev/null 2>&1 || alias ls='ls --color=auto'
alias grep >/dev/null 2>&1 || alias grep='grep --color=auto'
alias ll >/dev/null 2>&1 || alias ll='ls -alF'
alias la >/dev/null 2>&1 || alias la='ls -A'
alias l >/dev/null 2>&1 || alias l='ls -CF'
'''


def new_run_dir():
    _run_counter[0] += 1
    d = os.path.join(STATE_DIR, str(_run_counter[0]))
    os.mkdir(d)
    return d


def apply_state(run_dir):
    """Übernimmt Ordner, Variablen und Definitionen aus einem abgeschlossenen Lauf."""
    try:
        cwd = open(os.path.join(run_dir, "cwd"), errors="replace").read().rstrip("\n")
        envdata = open(os.path.join(run_dir, "env"), "rb").read()
        if envdata:
            new_env = dict(kv.decode(errors="replace").split("=", 1)
                           for kv in envdata.split(b"\0") if b"=" in kv)
            for k in ("SHLVL", "_", "BASH_EXECUTION_STRING"):
                new_env.pop(k, None)
            new_env["SHLVL"] = os.environ.get("SHLVL", "1")
            if new_env.get("PATH") != os.environ.get("PATH"):
                ENV._cmds = None
            os.environ.clear()
            os.environ.update(new_env)
        if cwd and os.path.isdir(cwd):
            os.chdir(cwd)
        defs = os.path.join(run_dir, "defs")
        if os.path.getsize(defs) > 0:
            os.replace(defs, DEFS)
            ENV.parse_defs(open(DEFS, errors="replace").read())
    except OSError:
        pass
    finally:
        shutil.rmtree(run_dir, True)


def load_startup(verbose=False):
    """Lädt ~/.profile (Login), ~/.bashrc und ~/.hamsterrc einmal in bash und übernimmt alles."""
    run_dir = new_run_dir()
    script = (f"__hs={shlex.quote(run_dir)}\n{SHOPTS}\n{DEFAULT_ALIASES}"
              f"[ -f ~/.hamsterrc ] && source ~/.hamsterrc\n{DUMP}")
    use_bashrc = os.environ.get("HAMSTER_NO_BASHRC") != "1"
    cmd = ["bash"] + (["-l"] if LOGIN and use_bashrc else []) + (["-i"] if use_bashrc else []) + ["-c", script]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10, start_new_session=True)
    except subprocess.TimeoutExpired:
        if verbose:
            print(f"{Y}~/.bashrc braucht zu lange – übersprungen.{RST}")
    apply_state(run_dir)


# ───────────────────────── Jobs (Strg+Z, fg, bg) ──────────────────
TTY = sys.stdin.fileno() if sys.stdin.isatty() else None
JOBS = []  # dicts: id, pid, cmd, stopped


def give_terminal(pgid):
    if TTY is not None:
        try:
            os.tcsetpgrp(TTY, pgid)
        except OSError:
            pass


def wait_fg(pid):
    """Wartet auf den Vordergrund-Prozess. Gibt (exit_code, gestoppt) zurück."""
    try:
        while True:
            try:
                _, st = os.waitpid(pid, os.WUNTRACED)
            except ChildProcessError:
                return 0, False
            except InterruptedError:
                continue
            if os.WIFSTOPPED(st):
                sig = os.WSTOPSIG(st)
                if sig in (signal.SIGTTIN, signal.SIGTTOU):
                    # Kind war schneller als die Terminal-Übergabe → nochmal übergeben
                    give_terminal(pid)
                    try:
                        os.killpg(pid, signal.SIGCONT)
                    except OSError:
                        pass
                    continue
                return 128 + sig, True
            if os.WIFEXITED(st):
                return os.WEXITSTATUS(st), False
            if os.WIFSIGNALED(st):
                return 128 + os.WTERMSIG(st), False
    finally:
        give_terminal(os.getpgrp())


def add_job(pid, cmd):
    jid = max([j["id"] for j in JOBS], default=0) + 1
    JOBS.append({"id": jid, "pid": pid, "cmd": cmd, "stopped": True})
    print(f"\n{Y}[{jid}] angehalten:{RST} {cmd}   {GR}→ weiter mit {BOLD}fg{RST}{GR}, im Hintergrund mit {BOLD}bg{RST}")


def reap_jobs():
    for j in list(JOBS):
        try:
            pid, st = os.waitpid(j["pid"], os.WNOHANG)
        except ChildProcessError:
            JOBS.remove(j)
            continue
        if pid:
            rc = os.WEXITSTATUS(st) if os.WIFEXITED(st) else 128 + os.WTERMSIG(st)
            col = G if rc == 0 else R
            print(f"{col}[{j['id']}] fertig (Exit-Code {rc}):{RST} {j['cmd']}")
            JOBS.remove(j)


def find_job(argv):
    if not JOBS:
        box("Keine Jobs", None, lines=[f"Es gibt keine angehaltenen Programme. ({BOLD}Strg+Z{RST} hält eins an.)"])
        return None
    if len(argv) < 2:
        return JOBS[-1]
    n = argv[1].lstrip("%")
    j = next((j for j in JOBS if str(j["id"]) == n), None)
    if not j:
        box(f"Job {argv[1]} gibt es nicht", None, lines=["Vorhanden: " + ", ".join(f"[{j['id']}] {j['cmd']}" for j in JOBS)])
    return j


# ───────────────────────── Ausführen ──────────────────────────────
def run_builtin(argv):
    cmd = argv[0]
    if cmd == "exit":
        sys.exit(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else ENV.last_rc)
    if cmd == "reload":
        load_startup(verbose=True)
        ENV._cmds = None
        print(f"{G}~/.bashrc und ~/.hamsterrc neu geladen.{RST}")
        return 0
    if cmd == "help":
        print(__doc__)
        print(f"Tasten: {BOLD}Tab{RST} vervollständigen · {BOLD}→ / Strg+E{RST} grauen Vorschlag annehmen · "
              f"{BOLD}Alt+→{RST} ein Wort annehmen · {BOLD}Strg+R{RST} im Verlauf suchen · "
              f"{BOLD}Strg+Z{RST} Programm anhalten ({BOLD}fg{RST}/{BOLD}bg{RST}/{BOLD}jobs{RST})\n"
              f"Deine ~/.bashrc (Aliase, Funktionen, PATH) wird automatisch geladen.\n"
              f"Eigene Einstellungen nur für hamster: {BOLD}~/.hamsterrc{RST} (bash-Syntax), danach {BOLD}reload{RST}")
        return 0
    if cmd == "jobs":
        reap_jobs()
        for j in JOBS:
            print(f"[{j['id']}] {'angehalten' if j['stopped'] else 'läuft    '}  {j['cmd']}")
        return 0
    if cmd in ("fg", "bg"):
        j = find_job(argv)
        if not j:
            return 1
        if cmd == "bg":
            os.killpg(j["pid"], signal.SIGCONT)
            j["stopped"] = False
            print(f"[{j['id']}] läuft im Hintergrund: {j['cmd']}")
            return 0
        print(j["cmd"])
        give_terminal(j["pid"])
        os.killpg(j["pid"], signal.SIGCONT)
        rc, stopped = wait_fg(j["pid"])
        JOBS.remove(j)
        if stopped:
            add_job(j["pid"], j["cmd"])
        return rc
    if cmd == "cd":
        target = os.path.expanduser(argv[1]) if len(argv) > 1 else HOME
        if target == "-":
            target = os.environ.get("OLDPWD", HOME)
        target = os.path.expandvars(target)
        try:
            old = os.getcwd()
            os.chdir(target)
            os.environ["OLDPWD"], os.environ["PWD"] = old, os.getcwd()
            return 0
        except FileNotFoundError:
            line = " ".join(argv)
            d, f = os.path.split(target.rstrip("/"))
            lines = [f"Den Ordner {BOLD}{argv[1]}{RST} gibt es nicht."]
            try:
                dirs = [e for e in os.listdir(d or ".") if os.path.isdir(os.path.join(d or ".", e))]
                s = suggest_names(f, dirs)
                if s:
                    lines.append(s)
            except OSError:
                lines.append(f"Schon {BOLD}{d}{RST} existiert nicht.")
            box("Ordner nicht gefunden", line, 3, len(line), lines)
        except NotADirectoryError:
            box("Das ist kein Ordner", " ".join(argv), 3, len(" ".join(argv)),
                [f"{argv[1]} ist eine Datei. Anschauen mit: {G}less {argv[1]}{RST}"])
        except PermissionError:
            box("Keine Berechtigung", " ".join(argv), 3, len(" ".join(argv)),
                [f"Du darfst {argv[1]} nicht betreten. Rechte ansehen: {G}ls -ld {argv[1]}{RST}"])
        return 1
    return None


class ErrReader(threading.Thread):
    """Liest stderr des Programms über ein Pseudo-Terminal, zeigt es sofort an und merkt es sich.
    Für das Programm sieht stderr wie ein echtes Terminal aus (Farben, Fortschrittsbalken)."""

    def __init__(self, fd):
        super().__init__(daemon=True)
        self.fd, self.buf = fd, bytearray()

    def run(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            view = memoryview(data)
            while view:
                try:
                    view = view[os.write(2, view):]
                except BlockingIOError:
                    continue
                except OSError:
                    break
            self.buf += data
            del self.buf[:-65536]
        os.close(self.fd)

    def lines(self):
        text = self.buf.decode(errors="replace")
        text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]", "", text)
        return [l.split("\r")[-1] if l.split("\r")[-1] else l.replace("\r", "")
                for l in text.replace("\r\n", "\n").split("\n")]


def is_implicit_cd(word):
    p = os.path.expanduser(word)
    return (word in ("..", "~") or word.endswith("/")) and os.path.isdir(p) and \
        not (os.path.isfile(p) and os.access(p, os.X_OK))


def main_command(line):
    """Der eigentliche Programmname (ohne sudo, env, VAR=wert ...)."""
    for t in tokenize(line):
        if t.cmdpos and t.text not in CMD_AFTER and t.text not in KEYWORDS:
            return os.path.basename(t.text), t
    return "", None


def execute(line):
    try:
        argv = shlex.split(line)
    except ValueError:
        argv = []
    simple = argv and not any(t.kind == "op" for t in tokenize(line))
    if simple and argv[0] in ("cd", "exit", "help", "reload", "jobs", "fg", "bg"):
        return run_builtin(argv)
    if simple and len(argv) == 1 and is_implicit_cd(argv[0]):
        return run_builtin(["cd", argv[0]])     # Ordnername allein → hineinwechseln (wie fish)

    # Syntax auch von bash prüfen lassen (fängt if/for/case-Fehler)
    chk = subprocess.run(["bash", "-n", "-O", "expand_aliases", "-O", "extglob", "-c", line], capture_output=True, text=True)
    if chk.returncode != 0:
        msg = chk.stderr.strip().splitlines()
        tokm = re.search(r"unexpected token [`'](.+?)'", chk.stderr)
        pos = line.find(tokm.group(1)) if tokm else -1
        box("Syntaxfehler", line, max(pos, 0), (pos + len(tokm.group(1))) if pos >= 0 else len(line),
            [re.sub(r"^bash: -c: (line|Zeile) \d+: ", "", l) for l in msg])
        return 2

    run_dir = new_run_dir()
    script = (f"__hs={shlex.quote(run_dir)}\n{SHOPTS}\n"
              f"source {shlex.quote(DEFS)} 2>/dev/null\n"
              f"{line}\n__hamster_rc=$?\n{AFTER_COMMAND}\n{DUMP}\nexit $__hamster_rc\n")

    prog, tok = main_command(line)
    args_after = line[tok.end:].split() if tok else []
    alias_target = ENV.aliases.get(prog, "").split()
    if alias_target:
        prog = os.path.basename(alias_target[0])
    capture = not (prog in INTERACTIVE and not (prog in REPL_WITH_SCRIPT and args_after))

    file_actions, reader, slave = [], None, None
    if capture:
        try:
            master, slave = pty.openpty()
            try:
                ws = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, ws)
            except OSError:
                pass
            file_actions = [(os.POSIX_SPAWN_DUP2, slave, 2)]
            reader = ErrReader(master)
        except OSError:
            capture = False

    sigs = [signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU,
            signal.SIGPIPE, signal.SIGCHLD]
    try:
        pid = os.posix_spawnp("bash", ["bash", "-c", script], os.environ,
                              file_actions=file_actions, setpgroup=0, setsigdef=sigs)
    finally:
        if slave is not None:
            os.close(slave)
    if reader:
        reader.start()
    give_terminal(pid)
    rc, stopped = wait_fg(pid)
    if stopped:
        add_job(pid, line)
        return rc
    if reader:
        reader.join(timeout=0.3)
    apply_state(run_dir)

    if rc != 0 and rc != 130:
        err_lines = reader.lines() if reader else []
        err_lines = [l for l in err_lines if l.strip()]
        expl = explain_stderr(err_lines, line) if err_lines else []
        exit_txt = explain_exit(rc)
        if expl is not None:
            lines = list(expl or [])
            if exit_txt and not lines:
                lines.append(exit_txt)
            if lines:
                box(f"Fehlgeschlagen (Exit-Code {rc})", None, lines=lines)
            elif rc > 128:
                box(f"Abgebrochen (Exit-Code {rc})", None, lines=[exit_txt or "Durch ein Signal beendet."])
    return rc


# ───────────────────────── Prompt ─────────────────────────────────
def git_branch():
    d = os.getcwd()
    while True:
        head = os.path.join(d, ".git", "HEAD")
        if os.path.isfile(head):
            ref = open(head).read().strip()
            return ref.rsplit("/", 1)[-1] if ref.startswith("ref:") else ref[:7]
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def starship_prompt():
    """Nutzt starship (https://starship.rs), wenn installiert – wie in bash/zsh/fish."""
    if os.environ.get("HAMSTER_PROMPT", "auto") not in ("auto", "starship") or not shutil.which("starship"):
        return None
    try:
        cols = shutil.get_terminal_size().columns
        env = dict(os.environ, STARSHIP_SHELL="", STARSHIP_SESSION_KEY=os.environ.get("STARSHIP_SESSION_KEY", str(os.getpid())))
        out = subprocess.run(["starship", "prompt", "--status", str(ENV.last_rc), "--cmd-duration",
                              str(int(ENV.last_duration * 1000)), "--jobs", str(len(JOBS)), "--terminal-width", str(cols)],
                             capture_output=True, text=True, timeout=1, env=env).stdout
        return ANSI(out) if out else None
    except Exception:
        return None


def make_prompt():
    sp = starship_prompt()
    if sp is not None:
        return sp
    cwd = os.getcwd()
    if cwd.startswith(HOME):
        cwd = "~" + cwd[len(HOME):]
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "ich"
    br = git_branch()
    rc = ENV.last_rc
    status = f"{R}[{rc}] {RST}" if rc else ""
    took = ""
    if ENV.last_duration >= 5:
        sec = int(ENV.last_duration)
        took = f" {Y}⏱ " + (f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else
                              f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s") + RST
    arrow = f"{R}❯{RST}" if rc else f"{G}❯{RST}"
    sym = "#" if os.geteuid() == 0 else ""
    return ANSI(f"{G}{user}{RST}{GR}@{socket.gethostname()}{RST} {B}{BOLD}{cwd}{RST}"
                + (f" {M}({br}){RST}" if br else "") + took + f"\n{status}{sym}{arrow} ")


STYLE = Style.from_dict({
    "cmd": "#5fd75f bold", "badcmd": "#ff5f5f bold underline", "error": "bg:#5f0000 #ffffff",
    "keyword": "#d787ff bold", "subcmd": "#87d7ff bold", "number": "#d7af5f", "glob": "#ffaf5f",
    "badpath": "#ff5f5f underline",
    "op": "#d787ff bold", "option": "#5fd7ff", "string": "#ffd75f", "var": "#ff87d7",
    "path": "underline", "comment": "#808080 italic", "auto-suggestion": "#666666",
})


def hangup_jobs():
    for j in JOBS:
        try:
            os.killpg(j["pid"], signal.SIGHUP)
            os.killpg(j["pid"], signal.SIGCONT)
        except OSError:
            pass


class HamsterHistory(FileHistory):
    """Speichert erst NACH der Verlaufs-Erweiterung (!! → echter Befehl), wie bash.
    Keine direkten Wiederholungen, Befehle mit Leerzeichen am Anfang werden nicht gespeichert."""

    def append_string(self, string):
        self.pending = string          # prompt_toolkit ruft das beim Enter auf – wir speichern später selbst

    def commit(self, string, raw):
        if not string.strip() or raw[:1] == " ":
            return
        newest = getattr(self, "_loaded_strings", None)
        if newest and newest[0] == string:
            return
        FileHistory.append_string(self, string)


HIST_RE = re.compile(r"!(!|\$|\^|\*|-?\d+|[A-Za-z_./~][^\s;|&()<>'\"!:]*)(:(?:\^|\$|\*|\d+))?")


def _words(cmd):
    return [t.text for t in tokenize(cmd) if t.kind in ("word", "assign", "funcdef")] or [cmd]


def expand_history(line, hist):
    """bash-Verlaufserweiterung: !! !$ !^ !* !-2 !42 !text ^alt^neu. Gibt (zeile, geändert) zurück."""
    m = re.match(r"\^([^^]+)\^([^^]*)\^?$", line)
    if m:
        if not hist or m.group(1) not in hist[-1]:
            raise ValueError(f"'{m.group(1)}' kommt im letzten Befehl nicht vor", 0, len(line))
        return hist[-1].replace(m.group(1), m.group(2), 1), True
    out, i, n, changed, in_sq, in_dq = [], 0, len(line), False, False, False
    while i < n:
        c = line[i]
        if c == "\\" and not in_sq:
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "'" and not in_dq:
            in_sq = not in_sq
        elif c == '"' and not in_sq:
            in_dq = not in_dq
        elif c == "!" and not in_sq and i + 1 < n and line[i + 1] not in " \t\n=(\"" \
                and (i == 0 or line[i - 1] not in "${[\\"):
            m = HIST_RE.match(line, i)
            if m:
                ev, mod = m.group(1), m.group(2)
                if ev in ("!", "$", "^", "*"):
                    entry = hist[-1] if hist else None
                elif ev.lstrip("-").isdigit():
                    k = int(ev)
                    entry = hist[k - 1] if 0 < k <= len(hist) else hist[k] if -len(hist) <= k < 0 else None
                else:
                    entry = next((h for h in reversed(hist) if h.startswith(ev)), None)
                if entry is None:
                    raise ValueError(f"{m.group(0)}: kein passender Befehl im Verlauf", i, m.end())
                words = _words(entry)
                sel = {"$": ":$", "^": ":^", "*": ":*"}.get(ev, mod)
                if sel == ":$":
                    rep = words[-1]
                elif sel == ":^":
                    rep = words[1] if len(words) > 1 else ""
                elif sel == ":*":
                    rep = " ".join(words[1:])
                elif sel:
                    k = int(sel[1:])
                    if k >= len(words):
                        raise ValueError(f"{m.group(0)}: der Befehl hat kein Wort Nr. {k}", i, m.end())
                    rep = words[k]
                else:
                    rep = entry
                out.append(rep)
                i, changed = m.end(), True
                continue
        out.append(c)
        i += 1
    return "".join(out), changed


CONTINUE_ENDINGS = ("|", "&&", "||", "\\")


def needs_more_input(text):
    """Soll Enter eine neue Zeile beginnen statt auszuführen? (wie fish)"""
    t = text.rstrip(" \t\n")   # auch Leerzeilen danach (CRLF beim Einfügen = zweimal Enter)
    if t.endswith(CONTINUE_ENDINGS) and not t.endswith("\\\\"):
        return True
    try:
        toks = tokenize(text)
    except Exception:
        return False
    return toks.open_blocks > 0 or toks.heredoc_open is not None


def shell_loop():
    kb = KeyBindings()
    hist = []   # ausgeführte Befehle (für !!, !$, Alt+.)

    @kb.add("enter")
    @kb.add("c-j")      # manche Terminals schicken beim Einfügen LF statt CR
    def _(event):
        """Unfertige Eingabe (| am Ende, if ohne fi, Here-Document ...) geht in der nächsten Zeile weiter."""
        b = event.current_buffer
        b.complete_state = None
        if b.document.is_cursor_at_the_end and needs_more_input(b.text):
            b.insert_text("\n")
        else:
            b.validate_and_handle()

    @kb.add("c-e")
    def _(event):
        b = event.current_buffer
        if b.suggestion:
            b.insert_text(b.suggestion.text)
        else:
            b.cursor_position = len(b.text)

    yank = {"k": 0, "ins": None, "pos": None}

    @kb.add("escape", ".")
    def _(event):
        """Alt+. fügt das letzte Wort des vorigen Befehls ein (nochmal drücken = noch weiter zurück)."""
        b = event.current_buffer
        if not hist:
            return
        if yank["ins"] is not None and b.cursor_position == yank["pos"] and \
                b.document.text_before_cursor.endswith(yank["ins"]):
            b.delete_before_cursor(len(yank["ins"]))
            yank["k"] += 1
        else:
            yank["k"] = 1
        if yank["k"] > len(hist):
            yank["k"] = 1
        w = _words(hist[-yank["k"]])[-1]
        b.insert_text(w)
        yank["ins"], yank["pos"] = w, b.cursor_position

    history = HamsterHistory(HISTFILE)
    session = PromptSession(history=history, auto_suggest=ThreadedAutoSuggest(HamsterSuggest()),
                            completer=ThreadedCompleter(HamsterCompleter()), lexer=HamsterLexer(), style=STYLE,
                            complete_while_typing=False, key_bindings=kb, enable_history_search=True,
                            mouse_support=False, multiline=True, complete_style=CompleteStyle.MULTI_COLUMN,
                            prompt_continuation=lambda width, line_no, wrap: ANSI(GR + "…".rjust(width - 1) + " " + RST))
    try:
        hist.extend(history.load_history_strings())
        hist.reverse()
    except Exception:
        pass
    if os.environ.get("HAMSTER_QUIET") != "1":
        print(f"{BOLD}🐹 hamster{RST} – Tippe {G}help{RST} für Hilfe. Tab = vervollständigen, → = Vorschlag annehmen.")
    warned_jobs = False
    while True:
        reap_jobs()
        try:
            raw = session.prompt(make_prompt())
        except KeyboardInterrupt:
            continue
        except EOFError:
            if JOBS and not warned_jobs:
                print(f"{Y}Es gibt noch angehaltene Programme (jobs). Nochmal Strg+D zum Beenden.{RST}")
                warned_jobs = True
                continue
            print("Tschüss!")
            break
        line = raw.strip()
        if not line:
            continue
        try:
            line, changed = expand_history(line, hist)
        except ValueError as e:
            msg, a, b = e.args
            box("Verlauf: " + msg, line, a, b)
            ENV.last_rc = 1
            continue
        if changed:
            print(f"{GR}{line}{RST}")
        history.commit(line, raw)
        hist.append(line)
        if line == "history" or re.fullmatch(r"history \d+", line):
            n = int(line.split()[1]) if " " in line else 50
            start = max(0, len(hist) - n)
            for i, h in enumerate(hist[start:], start + 1):
                print(f"{GR}{i:>5}{RST}  {h}")
            continue
        if not precheck(line):
            ENV.last_rc, ENV.last_duration = 127, 0
            continue
        started = time.monotonic()
        try:
            ENV.last_rc = execute(line)
        except SystemExit:
            raise
        except Exception as e:
            box("Interner Fehler in hamster", None, lines=[repr(e), "Der Befehl wurde evtl. nicht ausgeführt."])
            ENV.last_rc = 1
        ENV.last_duration = time.monotonic() - started


def main():
    os.environ["SHLVL"] = str(int(os.environ.get("SHLVL", "0") or 0) + 1)
    # Die Shell selbst darf durch Strg+Z / Terminal-Übergaben nicht angehalten werden
    # Strg+C trifft nur das laufende Programm (eigene Prozessgruppe), nie die Shell selbst
    for s in (signal.SIGINT, signal.SIGTSTP, signal.SIGTTOU, signal.SIGTTIN, signal.SIGQUIT):
        signal.signal(s, signal.SIG_IGN)
    if TTY is not None:
        try:
            os.setpgid(0, 0)
        except OSError:
            pass
        give_terminal(os.getpgrp())
    load_startup()
    ENV.warm_up()
    BASHCOMP.warm_up()

    if _REST[:1] == ["--try"]:
        cmd = " ".join(_REST[1:])
        sys.exit(execute(cmd) if precheck(cmd) else 127)

    atexit.register(hangup_jobs)
    try:
        shell_loop()
    except SystemExit:
        raise
    except BaseException as e:  # Sicherheitsnetz: bei einem Absturz in bash weitermachen
        sys.stderr.write(f"\nhamster ist abgestürzt ({e!r}) – starte bash, damit du weiterarbeiten kannst.\n")
        _fallback_to_bash([], False)


if __name__ == "__main__":
    main()
