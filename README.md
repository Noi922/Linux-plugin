# klar 🐚

Eine freundliche Linux-Shell im Stil von **fish**, die dir **klar und deutlich sagt, wo der Fehler ist**, und vervollständigt, was du eingibst.

## Funktionen

- **Farben beim Tippen:** Befehl grün = existiert, **rot unterstrichen = gibt es nicht**. Optionen, Strings, Variablen, Pipes und vorhandene Pfade sind farbig.
- **Grauer Vorschlag** aus dem Verlauf (wie bei fish). Mit `→` oder `Strg+E` übernimmst du ihn, mit `Alt+→` nur ein Wort.
- **Tab-Vervollständigung** für Befehle, Dateien und Ordner (`cd` zeigt nur Ordner), `$VARIABLEN`, Unterbefehle von git, apt, docker, systemctl, pip, npm und cargo sowie git-Branches. Bei Tippfehlern wird auch unscharf gesucht.
- **Fehler vor dem Ausführen** werden mit `^^^` an der genauen Stelle markiert:
  ```
  ✗ Befehl nicht gefunden
  │ gti status
  │ ^^^
  │ Den Befehl gti gibt es nicht.
  │ Meintest du: git, tic, gsutil ?
  ```
  Erkannt werden unbekannte Befehle, offene Anführungszeichen und `$(`, Pipes oder `&&` ohne Befehl, `>` ohne Datei und Bash-Syntaxfehler.
- **Fehler nach dem Ausführen** werden erklärt. Bei Python-Tracebacks, Compiler-Meldungen (gcc, clang, rustc …) und Shell-Skripten siehst du **Datei, Zeile und Code-Ausschnitt**:
  ```
  ✗ Fehlgeschlagen (Exit-Code 1)
  │ Fehler in t.c, Zeile 4, Spalte 3:
  │ expected ‘,’ or ‘;’ before ‘return’
  │      3 │   int x = 1
  │ →    4 │   return 0;
  │        │   ^ hier
  ```
  Dazu kommen verständliche Hinweise zu „Datei nicht gefunden“ (mit Namensvorschlag), „Keine Berechtigung“, voller Festplatte, DNS- und Netzwerkfehlern, apt, git, belegten Ports, Signalen wie Segfault oder OOM-Kill und mehr.
- Volle Bash-Kompatibilität beim Ausführen: Pipes, `&&`, Schleifen und `export` funktionieren. `cd` und Variablen bleiben erhalten.

## Installation

```bash
git clone https://github.com/Noi922/Linux-plugin.git
cd Linux-plugin
./install.sh
klar
```

Voraussetzung: Python 3.8 oder neuer mit `venv` (Debian/Ubuntu: `sudo apt install python3-venv`).

## Benutzung

| Taste | Wirkung |
|---|---|
| `Tab` | vervollständigen / Auswahl durchblättern |
| `→` oder `Strg+E` | grauen Vorschlag übernehmen |
| `Alt+→` | ein Wort vom Vorschlag übernehmen |
| `Strg+R` | im Verlauf suchen |
| `Strg+D` | beenden |

Eingebaute Befehle: `help`, `history`, `reload` (lädt `~/.klarrc` neu), `cd`, `exit`.

**Konfiguration:** `~/.klarrc` ist in Bash-Syntax geschrieben und wird vor jedem Befehl eingelesen, zum Beispiel:
```bash
alias gs='git status'
export EDITOR=nano
```

Einzelnen Befehl ausführen: `klar -c 'befehl'`
