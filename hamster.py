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

__version__ = "1.0"


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
    from prompt_toolkit.auto_suggest import AutoSuggest, AutoSuggestFromHistory, Suggestion
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.lexers import Lexer
    from prompt_toolkit.styles import Style
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
KEYWORDS = set("if then else elif fi for while until do done case esac in select function time { } ! [[ ]]".split())
CMD_AFTER = {"do", "then", "else", "elif", "while", "until", "if", "!", "{", "time",
             "sudo", "nohup", "exec", "command", "builtin", "nice", "watch", "env", "xargs", "doas"}
WRAPPER_ARG_OPTS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R"},
    "doas": {"-u", "-C"}, "nice": {"-n"}, "env": {"-u", "-C", "-S"},
    "xargs": {"-I", "-n", "-P", "-d", "-a", "-E", "-L", "-s"}, "watch": {"-n", "--interval"},
}
OPS = ["&&", "||", ">>", "2>&1", "&>", "2>", "|&", "|", ";", "&", ">", "<", "(", ")"]
CMD_OPS = {"&&", "||", "|", "|&", ";", "&", "("}
REDIR_OPS = {">", ">>", "<", "2>", "&>"}
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


# ─────────────────────────── Tokenizer ────────────────────────────
@dataclass
class Tok:
    text: str
    start: int
    end: int
    kind: str          # word | op | comment | assign
    cmdpos: bool = False
    unclosed: int = -1  # Position eines nicht geschlossenen Anführungszeichens


def tokenize(line):
    toks, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t\n":
            i += 1
            continue
        if c == "#" and (i == 0 or line[i - 1] in " \t;|&("):
            toks.append(Tok(line[i:], i, n, "comment"))
            break
        op = next((o for o in OPS if line.startswith(o, i)), None)
        if op:
            toks.append(Tok(op, i, i + len(op), "op"))
            i += len(op)
            continue
        start, unclosed = i, -1
        while i < n and line[i] not in " \t\n" and not any(line.startswith(o, i) for o in OPS):
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch in "'\"`":
                q, qpos = ch, i
                i += 1
                while i < n and line[i] != q:
                    i += 2 if (line[i] == "\\" and q != "'") else 1
                if i >= n:
                    unclosed = qpos
                    i = n
                    break
            elif ch == "$" and line.startswith("$(", i):
                depth, qpos = 0, i
                while i < n:
                    if line[i] == "(":
                        depth += 1
                    elif line[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                if i >= n:
                    unclosed = qpos
                    break
            i += 1
        i = min(i, n)
        toks.append(Tok(line[start:i], start, i, "word", unclosed=unclosed))

    # Funktionsdefinition "name() {" bzw. "function name" ist kein Befehlsaufruf
    fdefs = {id(toks[k]) for k in range(len(toks) - 2)
             if toks[k].kind == "word" and toks[k + 1].text == "(" and toks[k + 2].text == ")"}
    expect_cmd, skip_rest, after_redir = True, False, False
    prev_word = last_word = wrapper = None
    skip_next = False
    for t in toks:
        if id(t) in fdefs:
            t.kind, expect_cmd = "funcdef", True
            continue
        if t.kind == "op":
            if t.text in CMD_OPS or t.text == ")":
                expect_cmd, skip_rest = True, False
            after_redir = t.text in REDIR_OPS
            continue
        if t.kind != "word":
            continue
        last_word, prev_word = prev_word, t.text
        if after_redir:
            after_redir = False
            continue
        if skip_rest:
            continue
        if last_word == "function" and expect_cmd:
            t.kind = "funcdef"   # "function name {" → name ist neu, kein Aufruf
            continue
        if expect_cmd:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t.text):
                t.kind = "assign"
                continue
            if t.text.startswith("-") and wrapper:
                # Option von sudo/nice/env ...; manche haben einen Wert dahinter (sudo -u root)
                if t.text in WRAPPER_ARG_OPTS.get(wrapper, ()):
                    skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue
            t.cmdpos = True
            wrapper = os.path.basename(t.text) if t.text in CMD_AFTER else None
            if t.text in ("for", "case", "select"):
                skip_rest = True
            expect_cmd = t.text in CMD_AFTER or t.text == "function"
    return toks


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
class HamsterLexer(Lexer):
    def lex_document(self, document):
        line = document.text

        def get_line(_):
            frags, pos = [], 0
            for t in tokenize(line):
                if t.start > pos:
                    frags.append(("", line[pos:t.start]))
                if t.kind == "op":
                    style = "class:op"
                elif t.kind == "comment":
                    style = "class:comment"
                elif t.kind == "funcdef":
                    style = "class:cmd"
                elif t.kind == "assign":
                    style = "class:var"
                elif t.unclosed >= 0:
                    style = "class:error"
                elif t.cmdpos:
                    style = "class:cmd" if ENV.exists(t.text) else "class:badcmd"
                elif t.text.startswith("-"):
                    style = "class:option"
                elif t.text[:1] in "'\"":
                    style = "class:string"
                elif t.text.startswith("$"):
                    style = "class:var"
                elif os.path.exists(os.path.expanduser(t.text)):
                    style = "class:path"
                else:
                    style = ""
                frags.append((style, line[t.start:t.end]))
                pos = t.end
            frags.append(("", line[pos:]))
            return frags

        return get_line


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


class HamsterCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        toks = [t for t in tokenize(text) if t.kind != "comment"]
        if text and text[-1] in " \t" or not toks or toks[-1].kind == "op":
            word, cur = "", None
        else:
            cur = toks[-1]
            word = cur.text
        words_before = [t for t in toks if t is not cur]
        cmdpos = cur.cmdpos if cur else (not words_before or words_before[-1].kind == "op"
                                         or words_before[-1].text in CMD_AFTER)
        if word.startswith("$"):
            for k in sorted(os.environ):
                if k.startswith(word[1:]):
                    yield Completion("$" + k, -len(word), display_meta=os.environ[k][:40])
            return
        if cmdpos and "/" not in word:
            matches = sorted(c for c in ENV.commands() | KEYWORDS if c.startswith(word))
            if not matches and word:
                matches = difflib.get_close_matches(word, ENV.commands(), n=8, cutoff=0.6)
            for c in matches[:200]:
                meta = "Alias: " + ENV.aliases[c] if c in ENV.aliases else ("eingebaut" if c in BASH_BUILTINS else "")
                yield Completion(c, -len(word), display_meta=meta)
            return
        # Unterbefehle (git, apt, ...)
        cmd_tok = next((t for t in reversed(words_before) if t.cmdpos), None)
        if cmd_tok:
            cmd = os.path.basename(cmd_tok.text)
            args = [t for t in words_before if t.start > cmd_tok.start and t.kind == "word"]
            if cmd in SUBCOMMANDS and not args:
                for s in SUBCOMMANDS[cmd]:
                    if s.startswith(word):
                        yield Completion(s, -len(word), display_meta=cmd + " Unterbefehl")
            if cmd == "git" and args and args[0].text in ("checkout", "switch", "merge", "rebase", "branch"):
                try:
                    out = subprocess.run(["git", "branch", "-a", "--format=%(refname:short)"],
                                         capture_output=True, text=True, timeout=1).stdout.split()
                    for b in out:
                        if b.startswith(word):
                            yield Completion(b, -len(word), display_meta="Branch")
                except Exception:
                    pass
            only_dirs = cmd in ("cd", "pushd", "rmdir")
        else:
            only_dirs = False
        for shown, meta in path_completions(word, only_dirs):
            yield Completion(shown, -len(word), display_meta=meta)


class HamsterSuggest(AutoSuggest):
    """Erst aus dem Verlauf, sonst die eindeutige Vervollständigung (wie fish)."""
    hist = AutoSuggestFromHistory()
    comp = HamsterCompleter()

    def get_suggestion(self, buffer, document):
        s = self.hist.get_suggestion(buffer, document)
        if s or not document.text or document.text[-1] == " ":
            return s
        comps = list(self.comp.get_completions(document, None))
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
    real = [t for t in toks if t.kind != "comment"]
    for i, t in enumerate(real):
        if t.kind != "op" or t.text in ("&", ";", ")", "("):
            continue
        nxt = real[i + 1] if i + 1 < len(real) else None
        prev = real[i - 1] if i > 0 else None
        if t.text in REDIR_OPS and (nxt is None or nxt.kind == "op"):
            box(f"Syntaxfehler: nach '{t.text}' fehlt ein Dateiname", line, t.start, t.end,
                ["Beispiel: ls > liste.txt"])
            return False
        if t.text in ("|", "&&", "||", "|&"):
            if prev is None or prev.kind == "op" and prev.text != ")":
                box(f"Syntaxfehler: vor '{t.text}' steht kein Befehl", line, t.start, t.end)
                return False
            if nxt is None:
                box(f"Syntaxfehler: nach '{t.text}' fehlt ein Befehl", line, t.start, t.end,
                    ["Die Zeile endet mit einem Operator – da muss noch etwas kommen."])
                return False
    defined_here = {t.text for t in toks if t.kind == "funcdef"}
    for t in toks:
        if t.cmdpos and t.text not in defined_here and not ENV.exists(t.text):
            word = t.text
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

    rules = [
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
         f"Erlaubte Optionen: {G}{line.split()[0]} --help{RST}"]),
        (r"missing operand|fehlender Operand", ["Es fehlt ein Argument (z. B. Datei- oder Ordnername).",
         f"Hilfe: {G}{line.split()[0]} --help{RST}"]),
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
    for __f in $(compgen -A function); do [[ $__f == _* ]] || declare -f "$__f"; done
    for __v in $(compgen -v); do
      case $__v in BASH*|SHELLOPTS|EUID|UID|PPID|GROUPS|FUNCNAME|PIPESTATUS|RANDOM|SRANDOM|SECONDS|\
LINENO|HISTCMD|EPOCH*|COMP*|DIRSTACK|_|__*|SHLVL|OLDPWD|PWD|COLUMNS|LINES|OPT*|IFS|PS[0-4]|\
PROMPT_COMMAND|HOSTNAME|HOSTTYPE|MACHTYPE|OSTYPE|HIST*|MAILCHECK|TERM|READLINE*) continue;; esac
      __a=${!__v@a}; [[ $__a == *[rx]* ]] && continue
      declare -p "$__v"
    done
  } > "$__hs/defs"
} 2>/dev/null
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
    script = (f"__hs={shlex.quote(run_dir)}\nshopt -s expand_aliases\n{DEFAULT_ALIASES}"
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

    # Syntax auch von bash prüfen lassen (fängt if/for/case-Fehler)
    chk = subprocess.run(["bash", "-n", "-O", "expand_aliases", "-c", line], capture_output=True, text=True)
    if chk.returncode != 0:
        msg = chk.stderr.strip().splitlines()
        tokm = re.search(r"unexpected token [`'](.+?)'", chk.stderr)
        pos = line.find(tokm.group(1)) if tokm else -1
        box("Syntaxfehler", line, max(pos, 0), (pos + len(tokm.group(1))) if pos >= 0 else len(line),
            [re.sub(r"^bash: -c: (line|Zeile) \d+: ", "", l) for l in msg])
        return 2

    run_dir = new_run_dir()
    script = (f"__hs={shlex.quote(run_dir)}\nshopt -s expand_aliases\n"
              f"source {shlex.quote(DEFS)} 2>/dev/null\n"
              f"{line}\n__hamster_rc=$?\n{DUMP}\nexit $__hamster_rc\n")

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


def make_prompt():
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
    """Wie bash mit HISTCONTROL=ignoreboth: keine direkten Wiederholungen,
    Befehle mit Leerzeichen am Anfang werden nicht gespeichert."""

    def append_string(self, string):
        if not string.strip() or string[:1] == " ":
            return
        newest = getattr(self, "_loaded_strings", None)
        if newest and newest[0] == string:
            return
        super().append_string(string)


CONTINUE_ENDINGS = ("|", "&&", "||", "\\")


def shell_loop():
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        """Endet die Zeile mit | && || oder \\, geht es in der nächsten Zeile weiter."""
        b = event.current_buffer
        b.complete_state = None
        text = b.document.text_before_cursor.rstrip(" \t")
        if b.document.is_cursor_at_the_end and text.endswith(CONTINUE_ENDINGS) and not text.endswith("\\\\"):
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

    session = PromptSession(history=HamsterHistory(HISTFILE), auto_suggest=HamsterSuggest(),
                            completer=HamsterCompleter(), lexer=HamsterLexer(), style=STYLE,
                            complete_while_typing=False, key_bindings=kb, enable_history_search=False,
                            mouse_support=False, multiline=True,
                            prompt_continuation=lambda width, line_no, wrap: ANSI(GR + "…".rjust(width - 1) + " " + RST))
    if os.environ.get("HAMSTER_QUIET") != "1":
        print(f"{BOLD}🐹 hamster{RST} – Tippe {G}help{RST} für Hilfe. Tab = vervollständigen, → = Vorschlag annehmen.")
    warned_jobs = False
    while True:
        reap_jobs()
        try:
            line = session.prompt(make_prompt())
        except KeyboardInterrupt:
            continue
        except EOFError:
            if JOBS and not warned_jobs:
                print(f"{Y}Es gibt noch angehaltene Programme (jobs). Nochmal Strg+D zum Beenden.{RST}")
                warned_jobs = True
                continue
            print("Tschüss!")
            break
        line = line.strip()
        if not line:
            continue
        if line == "history":
            for i, h in enumerate(reversed(list(session.history.get_strings())[-50:]), 1):
                print(f"{GR}{i:>3}{RST}  {h}")
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
