# 🐹 hamster

[![Lizenz: MIT](https://img.shields.io/badge/Lizenz-MIT-blue.svg)](LICENSE)

Eine freundliche Linux-Shell im Stil von **fish**, die dir **klar und deutlich sagt, wo der Fehler ist**, und vervollständigt, was du eingibst. Für **Ubuntu** und **Debian**.

## Installation

Ein Befehl im Terminal genügt:

```bash
curl -fsSL https://raw.githubusercontent.com/Noi922/Linux-plugin/main/packaging/setup.sh | sudo sh
```

Danach startest du hamster einfach mit:

```bash
hamster
```

Ohne curl geht es mit wget: `wget -qO- https://raw.githubusercontent.com/Noi922/Linux-plugin/main/packaging/setup.sh | sudo sh`

Das Skript richtet die apt-Paketquelle ein, sobald sie online ist. Danach geht alles wie bei jedem anderen Paket:

```bash
sudo apt install hamster                  # installieren
sudo apt update && sudo apt upgrade       # aktualisieren
sudo apt remove hamster                   # entfernen
```

Ist die Paketquelle nicht erreichbar, installiert das Skript die neueste `.deb`-Datei aus den [Releases](https://github.com/Noi922/Linux-plugin/releases). Zum Aktualisieren führst du dann denselben Befehl einfach nochmal aus.

<details><summary>Andere Wege</summary>

Paketquelle von Hand einrichten:
```bash
echo "deb [trusted=yes] https://noi922.github.io/Linux-plugin/ ./" | sudo tee /etc/apt/sources.list.d/hamster.list
sudo apt update && sudo apt install hamster
```

Aus dem Quellcode:
```bash
git clone https://github.com/Noi922/Linux-plugin.git && cd Linux-plugin && ./install.sh
```
</details>

**Unterstützt:** Ubuntu 22.04, 24.04 und neuer · Debian 12, 13. Jede Version wird automatisch getestet.

## Starten

```bash
hamster                       # ausprobieren
chsh -s /usr/bin/hamster      # als Standard-Shell (Zurück: chsh -s /bin/bash)
```

## Was hamster kann

**Fehler vor dem Ausführen** werden mit `^^^` an der genauen Stelle markiert:
```
✗ Befehl nicht gefunden
│ gti status
│ ^^^
│ Den Befehl gti gibt es nicht.
│ Meintest du: git, tic, gsutil ?
```
Erkannt werden unbekannte Befehle (mit Paket-Vorschlag), nicht geschlossene `"` und `$(`, `|` oder `&&` ohne Befehl, `>` ohne Datei und Bash-Syntaxfehler.

**Fehler nach dem Ausführen** werden erklärt. Bei Python, gcc, clang, rustc und Shell-Skripten siehst du Datei, Zeile und Code:
```
✗ Fehlgeschlagen (Exit-Code 1)
│ Fehler in t.c, Zeile 3, Spalte 3:
│ expected ‘,’ or ‘;’ before ‘return’
│      2 │   int x = 1
│ →    3 │   return 0;
│        │   ^ hier
```
Dazu gibt es verständliche Hinweise zu: Datei nicht gefunden (mit Namensvorschlag), keine Berechtigung, Festplatte voll, DNS- und Netzwerkfehler, apt, git, belegte Ports, Segfault, OOM-Kill und mehr.

**Vervollständigung für jedes Wort:**
- `Tab` vervollständigt Befehle, Unterbefehle, Optionen und Argumente für alle Programme, die bash-completion kennt. Das sind über 800, zum Beispiel git, apt, systemctl, docker, ssh, tar, kill und pip.
  Beispiele: `git chec` → `checkout`, `ls --hum` → `--human-readable`, `systemctl res` → `restart`, `ssh -o` → alle SSH-Optionen.
- Auch Dateien und Ordner (bei `cd` nur Ordner), `$VARIABLEN` und git-Branches werden vervollständigt.
- Ein grauer Vorschlag aus dem Verlauf erscheint beim Tippen. Übernehmen mit `→` oder `Strg+E`.
- `↑` durchsucht den Verlauf nach dem, was du schon getippt hast.

**Farben für jedes Wort beim Tippen:**
- Befehle grün, **unbekannte Befehle rot**
- Schlüsselwörter (if, for, do …) lila, Unterbefehle (`git commit`) blau
- Optionen türkis, Strings gelb, `$VARIABLEN` rosa, Zahlen und Platzhalter (`*.txt`) eigene Farben
- Vorhandene Dateien und Ordner sind unterstrichen. Fehlende Ordner bei `cd` und fehlende Eingabedateien bei `<` sind rot.

**Komfort wie in fish:**
- Endet eine Zeile mit `|`, `&&` oder `\`, geht die Eingabe einfach in der nächsten Zeile weiter. Dasselbe gilt für Blöcke wie `if … fi`, `for … done` und Here-Documents.
- Ein Ordnername allein wechselt in den Ordner, zum Beispiel `..` oder `/etc/`.
- Dauert ein Befehl 5 Sekunden oder länger, zeigt der Prompt die Laufzeit an (`⏱ 1m 12s`).
- Der Verlauf speichert keine direkten Wiederholungen. Befehle mit einem Leerzeichen am Anfang landen gar nicht im Verlauf.

## Kompatibel mit deinen Programmen

hamster führt jeden Befehl in **echter bash** aus. Deshalb funktioniert alles wie gewohnt:

- hamster versteht die komplette bash-Syntax: `2>&1`, `>&2`, `&>>`, `[[ … ]]`, `(( … ))`, Arrays, `case`, Here-Documents, `<<<`, `<(…)`, `$(…)`, `sudo`, `timeout`, `find -exec` und mehr. Es gibt keine Fehlalarme bei gültigen Befehlen. Das wird bei jedem Push mit 48 Beispielen getestet.
- Verlaufserweiterung wie in bash: `sudo !!`, `!$`, `!*`, `!-2`, `!text`, `^alt^neu`. `Alt+.` fügt das letzte Wort des vorigen Befehls ein.
- Deine `~/.bashrc` und `~/.profile` werden geladen, mit Aliasen, Funktionen und `PATH`.
- Tools, die sich in bash einklinken, funktionieren: zoxide, direnv, conda, nvm, pyenv, kubectl-Vervollständigung und `PROMPT_COMMAND`.
- Wenn [starship](https://starship.rs) installiert ist, nutzt hamster dessen Prompt.
- `source venv/bin/activate`, `export`, `cd`, Funktionen und Variablen bleiben bis zum nächsten Befehl erhalten
- Programme sehen ein echtes Terminal: Farben und Fortschrittsbalken von apt, pip, git und gcc bleiben
- Vollbild-Programme laufen ganz normal: vim, nano, less, htop, top, tmux, ssh, mc …
- `Strg+C` beendet nur das Programm, `Strg+Z` hält es an, mit `fg`, `bg` und `jobs` geht es weiter
- Wird hamster von anderen Programmen aufgerufen (`ssh host befehl`, scp, rsync, VS Code Remote, cron, `hamster -c …`, Skripte), läuft direkt bash. Als Login-Shell geht also nichts kaputt.
- Sicherheitsnetz: Fehlt etwas oder stürzt hamster ab, startet automatisch bash. So stehst du nie ohne Shell da.

## Bedienung

| Taste | Wirkung |
|---|---|
| `Tab` | vervollständigen |
| `→` / `Strg+E` | grauen Vorschlag übernehmen |
| `Alt+→` | ein Wort vom Vorschlag übernehmen |
| `Strg+R` | im Verlauf suchen |
| `↑` | Verlauf nach dem bisher Getippten durchsuchen |
| `Alt+.` | letztes Wort des vorigen Befehls einfügen |
| `Strg+Z` | Programm anhalten (`fg` / `bg` / `jobs`) |
| `Strg+D` | beenden |

Eingebaute Befehle: `help`, `history`, `reload`, `jobs`, `fg`, `bg`, `cd`, `exit` · Handbuch: `man hamster`

**Einstellungen nur für hamster** kommen in `~/.hamsterrc` (Bash-Syntax), danach `reload` eingeben:
```bash
alias gs='git status'
export EDITOR=nano
```
Umgebungsvariablen:

| Variable | Wirkung |
|---|---|
| `HAMSTER_NO_BASHRC=1` | `~/.bashrc` nicht laden |
| `HAMSTER_QUIET=1` | keine Begrüßung |
| `HAMSTER_PROMPT=builtin` | eigenen Prompt statt starship verwenden |
| `HAMSTER_NO_BASH_COMPLETION=1` | bash-completion nicht verwenden |
| `HAMSTER_NO_PROMPT_COMMAND=1` | `PROMPT_COMMAND` nicht ausführen |

## Entwicklung

```bash
python3 hamster.py                              # direkt starten (braucht python3-prompt-toolkit)
python3 tests/test_hamster.py                   # Tests (braucht python3-pexpect)
packaging/build-deb.sh 1.0.0 dist               # .deb bauen
./install.sh                                    # aus dem Quellcode bauen und mit apt installieren
```

Bei jedem Push auf `main` passiert automatisch Folgendes ([Workflow](.github/workflows/release.yml)):
1. Das `.deb` wird gebaut, Version `VERSION` plus Build-Nummer.
2. Es wird auf Ubuntu 22.04/24.04 und Debian 12/13 mit apt installiert und getestet.
3. Es erscheint als GitHub-Release, und die apt-Paketquelle auf GitHub Pages wird aktualisiert. `sudo apt upgrade` holt dann die neue Version.

## Lizenz

hamster steht unter der [MIT-Lizenz](LICENSE). Du darfst es frei benutzen, verändern und weitergeben, auch kommerziell. Der Copyright-Hinweis muss dabei erhalten bleiben.
