#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Baut das Debian-Paket: packaging/build-deb.sh [VERSION] [AUSGABEORDNER]
# Ergebnis läuft auf Ubuntu (20.04+) und Debian (11+), Architektur "all".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-$(cat "$ROOT/VERSION")}"
OUT="${2:-$ROOT/dist}"
umask 022
B="$(mktemp -d)"
chmod 755 "$B"
trap 'rm -rf "$B"' EXIT

install -Dm755 "$ROOT/hamster.py" "$B/usr/lib/hamster/hamster.py"
sed -i -e '1s|.*|#!/usr/bin/python3|' -e "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$B/usr/lib/hamster/hamster.py"
mkdir -p "$B/usr/bin"
ln -s ../lib/hamster/hamster.py "$B/usr/bin/hamster"
install -Dm644 "$ROOT/README.md" "$B/usr/share/doc/hamster/README.md"
install -Dm644 "$ROOT/packaging/copyright" "$B/usr/share/doc/hamster/copyright"
install -Dm644 "$ROOT/packaging/hamster.1" "$B/usr/share/man/man1/hamster.1"
gzip -9n "$B/usr/share/man/man1/hamster.1"

mkdir -p "$B/DEBIAN"
cat > "$B/DEBIAN/control" <<EOC
Package: hamster
Version: $VERSION
Architecture: all
Maintainer: Noi922 <Noi922@users.noreply.github.com>
Depends: python3 (>= 3.8), python3-prompt-toolkit (>= 3.0), bash, bash-completion
Recommends: command-not-found
Section: shells
Priority: optional
Homepage: https://github.com/Noi922/Linux-plugin
Installed-Size: $(du -sk "$B/usr" | cut -f1)
Description: freundliche Shell im Stil von fish mit klaren Fehlermeldungen
 hamster markiert Fehler mit ^^^ genau an der richtigen Stelle, erklärt
 Fehlermeldungen (Datei, Zeile, Ursache, Lösungsvorschlag) und vervollständigt
 Befehle, Dateien und Unterbefehle. Grauer Vorschlag aus dem Verlauf wie bei fish.
 Läuft auf echter bash: ~/.bashrc, Aliase, Funktionen, Skripte und Programme
 funktionieren wie gewohnt.
EOC
cat > "$B/DEBIAN/postinst" <<'EOC'
#!/bin/sh
set -e
if [ "$1" = "configure" ] && command -v add-shell >/dev/null 2>&1; then
    add-shell /usr/bin/hamster
fi
EOC
cat > "$B/DEBIAN/prerm" <<'EOC'
#!/bin/sh
set -e
if [ "$1" = "remove" ] && grep -q ':/usr/bin/hamster$' /etc/passwd 2>/dev/null; then
    echo "ACHTUNG: hamster ist noch Login-Shell von:" >&2
    grep ':/usr/bin/hamster$' /etc/passwd | cut -d: -f1 | sed 's/^/  /' >&2
    echo "Vorher zurückstellen mit: chsh -s /bin/bash BENUTZER" >&2
fi
EOC
cat > "$B/DEBIAN/postrm" <<'EOC'
#!/bin/sh
set -e
if { [ "$1" = "remove" ] || [ "$1" = "purge" ]; } && command -v remove-shell >/dev/null 2>&1; then
    remove-shell /usr/bin/hamster
fi
EOC
chmod 755 "$B/DEBIAN/postinst" "$B/DEBIAN/prerm" "$B/DEBIAN/postrm"

mkdir -p "$OUT"
dpkg-deb -Zxz --root-owner-group --build "$B" "$OUT/hamster_${VERSION}_all.deb" >/dev/null
echo "$OUT/hamster_${VERSION}_all.deb"
