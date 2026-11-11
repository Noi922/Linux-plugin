#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests für hamster. Aufruf: python3 tests/test_hamster.py [PFAD-ZU-HAMSTER]
Braucht: python3-pexpect (sudo apt install python3-pexpect)"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pexpect

HERE = os.path.dirname(os.path.abspath(__file__))
HAMSTER = sys.argv[1] if len(sys.argv) > 1 else f"{sys.executable} {os.path.join(HERE, '..', 'hamster.py')}"
TMP = tempfile.mkdtemp(prefix="hamster-test-")
HOME = os.path.join(TMP, "home")
os.makedirs(HOME)
with open(os.path.join(HOME, ".bashrc"), "w") as f:
    f.write("case $- in *i*) ;; *) return;; esac\n"
            "alias hallo='echo HALLO-AUS-BASHRC'\n"
            "meinefunk() { echo \"FUNK $1\"; }\n"
            "export AUS_BASHRC=ja\n")
ENVIRON = {"HOME": HOME, "PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "xterm-256color",
           "USER": "test", "SHLVL": "1", "LANG": "C.UTF-8"}
fails = 0


def strip(s):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", s)


def check(name, ok, out=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + ("" if ok else "\n     " + repr(out[-400:])))
    fails += not ok


def try_cmd(cmd):
    r = subprocess.run(HAMSTER.split() + ["--try", cmd], capture_output=True, text=True,
                       env=ENVIRON, cwd=TMP, timeout=60)
    return r.returncode, strip(r.stdout + r.stderr)


# ── Fehlererkennung (nicht interaktiv) ──
with open(os.path.join(TMP, "t.py"), "w") as f:
    f.write("def f(x):\n    return x / 0\nf(1)\n")
with open(os.path.join(TMP, "t.sh"), "w") as f:
    f.write("echo eins\nnichtvorhanden123\n")

rc, out = try_cmd("gti status")
check("Tippfehler gti → git", rc == 127 and "^^^" in out and "Meintest du: git" in out, out)
rc, out = try_cmd('echo "offen')
check("offenes Anführungszeichen", "nicht geschlossen" in out, out)
rc, out = try_cmd("ls |")
check("Pipe ohne Befehl", "fehlt ein Befehl" in out, out)
rc, out = try_cmd("cat >")
check("> ohne Datei", "fehlt ein Dateiname" in out, out)
rc, out = try_cmd("ls nichtda")
check("Datei fehlt", "nichtda existiert nicht" in out, out)
rc, out = try_cmd("python3 t.py")
check("Python-Traceback mit Zeile", "Zeile 2" in out and "return x / 0" in out, out)
rc, out = try_cmd("bash t.sh")
check("Shell-Skript mit Zeile", "Zeile 2" in out, out)
rc, out = try_cmd("neu() { echo NEU-$1; }; neu ok; sudo -n -u root true 2>/dev/null; nice -n 5 echo nice")
check("Funktionen/sudo -u/nice -n", "NEU-ok" in out and "nice" in out and "nicht gefunden" not in out, out)
if shutil.which("gcc"):
    with open(os.path.join(TMP, "t.c"), "w") as f:
        f.write("int main(){\n  int x = 1\n  return 0;\n}\n")
    rc, out = try_cmd("gcc t.c -o t")
    check("gcc mit Zeile und Spalte", "Zeile 3" in out and "^ hier" in out, out)

# ── Kein Fehlalarm bei gültiger bash-Syntax ──
VALID_BASH = [
 "# Typische, korrekte Befehle – hamster darf hier KEINEN Fehler melden",
 "ls 2>&1 | head -1",
 "echo fehler >&2",
 "echo x 1>&2",
 "ls /nix 2>/dev/null || true",
 "exec 3>&1; echo drei >&3",
 "[[ -f /etc/passwd && -d /tmp ]] && echo ja",
 "(( i = 1 + 2 )); echo $i",
 "arr=(1 2 3); echo ${arr[1]}",
 "\\ls -d /tmp",
 "cat <<< \"hallo\"",
 "case x in a) echo a;; b) echo b;; *) echo stern;; esac",
 "if [ -d /tmp ]; then echo d; elif true; then echo e; else echo f; fi",
 "while read -r l; do echo $l; done <<< \"z\"",
 "for f in a b; do echo $f; done",
 "time -p true",
 "echo $(( 3 * 4 ))",
 "x=1 y=2 env | grep -c '^x=1'",
 "diff <(echo a) <(echo a) && echo gleich",
 "echo a &>/dev/null; echo b &>> /dev/null",
 "{ echo block; }",
 "echo ok >| /dev/null",
 "coproc cat; echo co",
 "select x in a; do break; done </dev/null",
 "f() { echo f; }; f",
 "function g() { echo g; }; g",
 "sudo -u root -- ls / >/dev/null; echo s",
 "find /etc -maxdepth 0 -exec ls -d {} \\;",
 "xargs -n1 echo <<< \"a b\"",
 "env -i PATH=/usr/bin ls -d /",
 "nice -n 5 ionice -c3 true",
 "echo ${HOME} \"$(echo \"nested $(echo deep)\")\" `echo bq`",
 "ls -d /tmp && { cd /tmp; pwd; }",
 "[ -d /tmp ] && echo klammer",
 "for ((i=0;i<2;i++)); do echo $i; done",
 "echo \"a;b|c\" 'd&&e' \\; done",
 "cat <<EOF\nheredoc zeile\nEOF",
 "cat <<-ENDE\n\tmit tab\n\tENDE",
 "if true\nthen echo mehrzeilig\nfi",
 "ls |\ngrep -c x || true",
 "echo $((1+2)) ${#HOME} ${HOME:0:1}",
 "a=1; a+=2; arr[0]=x; echo $a ${arr[0]}",
 "shopt -s extglob; ls -d /tm@(p|x)",
 "trap \"echo t\" EXIT; true",
 "printf \"%s\\n\" a b | while read -r x; do echo \"<$x>\"; done",
 "cd /tmp; ls >/dev/null 2>&1 & wait",
 "test -n \"$HOME\" && echo test",
 "! false && echo nicht"
]
fehlalarme = []
for c in VALID_BASH:
    rc, out = try_cmd(c)
    if "✗" in out:
        fehlalarme.append((c, out.strip().splitlines()[:3]))
check(f"{len(VALID_BASH)} gültige bash-Befehle ohne Fehlalarm", not fehlalarme, repr(fehlalarme[:3]))
# Mehrzeiliger Befehl mit \ am Zeilenende (z. B. Docker-Anleitung zum Kopieren)
DOCKER_SNIPPET = ('echo \\\n  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] '
                  'https://download.docker.com/linux/ubuntu \\\n  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \\\n'
                  '  tee ' + os.path.join(TMP, "docker.list") + ' > /dev/null\necho ZWEITE-ZEILE')
rc, out = try_cmd(DOCKER_SNIPPET)
check("Zeilenfortsetzung mit \\ (Docker-Anleitung)", "✗" not in out and "ZWEITE-ZEILE" in out
      and "download.docker.com" in open(os.path.join(TMP, "docker.list")).read(), out)
rc, out = try_cmd("git comit -m x")
check("git-Tippfehler wird erklärt", "commit" in out and "^^^^^" in out or not shutil.which("git"), out)

# ── Weitergabe an bash (Kompatibilität) ──
r = subprocess.run(HAMSTER.split() + ["-c", "echo $0; exit 3"], capture_output=True, text=True, env=ENVIRON)
check("-c läuft in bash", r.stdout.strip() == "bash" and r.returncode == 3, r.stdout)
r = subprocess.run(HAMSTER.split(), input="echo aus-pipe\n", capture_output=True, text=True, env=ENVIRON)
check("Befehle über Pipe", "aus-pipe" in r.stdout, r.stdout + r.stderr)
r = subprocess.run(HAMSTER.split() + [os.path.join(TMP, "t.sh")], capture_output=True, text=True, env=ENVIRON)
check("Skript als Argument", "eins" in r.stdout, r.stdout)

# ── Interaktiv ──
p = pexpect.spawn(HAMSTER, encoding="utf-8", dimensions=(40, 150), env=ENVIRON, cwd=TMP, timeout=30)


def drain(q=1.0):
    out = ""
    while True:
        try:
            out += p.read_nonblocking(100000, timeout=q)
        except pexpect.TIMEOUT:
            return strip(out)


def run(c, q=1.0):
    p.send(c + "\r")
    return drain(q)


drain(3)
for c, want in [("hallo", "HALLO-AUS-BASHRC"), ("meinefunk x", "FUNK x"), ("echo $AUS_BASHRC", "ja"),
                ("neu() { echo NEU-$1; }; x=42", ""), ("neu ok; echo var=$x", "var=42"),
                ("cd /usr && pwd", "/usr"), ("pwd", "/usr"), ("cd -", ""), ("pwd", TMP),
                ('python3 -c "import sys;print(sys.stderr.isatty(), sys.stdout.isatty())"', "True True"),
                ("export NEUVAR=1", ""), ("printenv NEUVAR", "1"), ("cat /etc/hostname | wc -l", "1")]:
    out = run(c)
    check("interaktiv: " + c, want in out, out)

p.send("ech")
time.sleep(1)
p.sendcontrol("e")
out = run("")
check("grauer Vorschlag + Strg+E", "echo $AUS_BASHRC" in out and "\nja" in out, out)

p.send("cd /us\t")
time.sleep(1)
out = run("; pwd")
check("Tab-Vervollständigung", "/usr" in out, out)

# Fortsetzungszeile: endet die Zeile mit |, geht es in der nächsten weiter
p.send("echo fortsetzung |\r")
time.sleep(0.5)
out = run("tr a-z A-Z")
check("Fortsetzungszeile nach |", "FORTSETZUNG" in out, out)
out = run("sleep 5.2; echo DAUER", 1)
time.sleep(5)
out += drain()
check("Dauer im Prompt", "⏱ 5s" in out, out)
for _ in range(3):
    run("echo doppelt")
run(" echo geheim")
hist = open(os.path.join(HOME, ".hamster_history")).read()
check("Verlauf ohne Duplikate/Leerzeichen-Befehle", hist.count("+echo doppelt") == 1 and "geheim" not in hist, hist[-300:])

# Einfügen mehrzeiliger Befehle: mit Bracketed Paste, als LF und als CRLF
for name, data in [("Bracketed Paste", "\x1b[200~" + DOCKER_SNIPPET.replace("ZWEITE-ZEILE", "PASTE-A") + "\x1b[201~\r"),
                   ("LF", DOCKER_SNIPPET.replace("ZWEITE-ZEILE", "PASTE-B") + "\n"),
                   ("CRLF", DOCKER_SNIPPET.replace("ZWEITE-ZEILE", "PASTE-C").replace("\n", "\r\n") + "\r\n")]:
    p.send(data)
    out = drain(2.5)
    tag = re.search(r"PASTE-\w", data).group(0)
    check("Einfügen mehrzeilig (" + name + ")", "nicht gefunden" not in out and "^J" not in out
          and (tag + "\r\n") in out.replace(tag + "\r\r\n", ""), out)

# Verlaufserweiterung wie in bash
run("echo eins zwei drei")
out = run("echo !$")
check("!$ = letztes Wort", "\r\ndrei\r\n" in out, out)
out = run("!!")
check("!! = letzter Befehl", "echo drei" in out and "\r\ndrei\r\n" in out, out)
out = run("^drei^vier")
check("^alt^neu", "vier" in out, out)
out = run("!echo")
check("!text", "vier" in out, out)
run("echo alpha beta")
p.send("echo ")
p.send("\x1b.")
time.sleep(0.5)
out = run("")
check("Alt+. fügt letztes Wort ein", "\r\nbeta\r\n" in out, out)
# Ordnername allein → hineinwechseln
run("cd /usr/share")
run("..")
out = run("pwd")
check("'..' wechselt den Ordner", "\r\n/usr\r\n" in out, out)
run("cd " + TMP)
# Mehrzeilige Blöcke wie in fish
p.send("if true\r")
time.sleep(0.4)
p.send("then echo im-block\r")
time.sleep(0.4)
out = run("fi")
check("if … fi über mehrere Zeilen", "im-block" in out, out)
# Funktionen mit _ (zoxide, conda ...) bleiben erhalten, PROMPT_COMMAND läuft
run("__tool_x() { echo TOOL-OK; }; tool() { __tool_x; }")
out = run("tool")
check("Hilfsfunktionen mit __ bleiben erhalten", "TOOL-OK" in out, out)
run("PROMPT_COMMAND='echo PC-LAEUFT >> " + os.path.join(TMP, "pc.txt") + "'")
run("true")
check("PROMPT_COMMAND läuft nach Befehlen", os.path.exists(os.path.join(TMP, "pc.txt")), "")
run("PROMPT_COMMAND=")
# Tab-Vervollständigung aller Wörter über bash-completion
if os.path.exists("/usr/share/bash-completion/bash_completion") and shutil.which("git"):
    p.send("git chec\t")
    time.sleep(2)
    out = run("--help >/dev/null; echo FERTIG", 1.5)
    check("Tab vervollständigt Unterbefehle (git chec → checkout)", "checkout" in out, out)
    p.send("ls --hum\t")
    time.sleep(2)
    out = run(" / >/dev/null; echo FERTIG2", 1.5)
    check("Tab vervollständigt Optionen (ls --hum → --human-readable)", "--human-readable" in out, out)

p.send("sleep 30\r")
time.sleep(1)
p.sendcontrol("z")
out = drain()
check("Strg+Z hält an", "angehalten" in out, out)
out = run("jobs")
check("jobs", "sleep 30" in out, out)
p.send("fg\r")
time.sleep(1)
p.sendcontrol("c")
drain()
out = run("jobs; echo ENDE")
check("fg + Strg+C", "sleep 30" not in out.split("ENDE")[-2] if "ENDE" in out else False, out)
p.send("sleep 20\r")
time.sleep(0.8)
p.sendcontrol("c")
out = run("echo lebt")
check("Strg+C trifft nur das Programm", "lebt" in out, out)
out = run('read -p "Name? " n; echo "Hallo $n"', 1)
p.send("Tom\r")
out = drain()
check("Eingabe (read)", "Hallo Tom" in out, out)
for prog, keys in [("vi " + os.path.join(TMP, "v.txt"), "iHallo\x1b:wq\r"), ("less /etc/passwd", "q"), ("top", "q")]:
    if shutil.which(prog.split()[0]):
        p.send(prog + "\r")
        drain(1.5)
        p.send(keys)
        drain(1.5)
        out = run("echo ZURUECK")
        check("Vollbild-Programm: " + prog.split()[0], "ZURUECK\r\n" in out, out)
p.send("exit\r")
p.expect(pexpect.EOF)
check("exit", True)

shutil.rmtree(TMP, True)
print(f"\n{fails} Fehler")
sys.exit(1 if fails else 0)
