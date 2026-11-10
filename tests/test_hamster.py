#!/usr/bin/env python3
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
