#!/bin/sh
# Installiert hamster auf Ubuntu & Debian – danach startet es mit dem Befehl: hamster
#   curl -fsSL https://raw.githubusercontent.com/Noi922/Linux-plugin/main/packaging/setup.sh | sudo sh
#   wget -qO-  https://raw.githubusercontent.com/Noi922/Linux-plugin/main/packaging/setup.sh | sudo sh
set -e
URL="${HAMSTER_REPO_URL:-https://noi922.github.io/Linux-plugin}"
DEB_URL="${HAMSTER_DEB_URL:-https://github.com/Noi922/Linux-plugin/releases/latest/download/hamster_all.deb}"
if [ "$(id -u)" != 0 ]; then
    echo "Bitte mit sudo ausführen:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/Noi922/Linux-plugin/main/packaging/setup.sh | sudo sh" >&2
    exit 1
fi
fetch() { if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"; else wget -qO- "$1"; fi; }

# Ubuntu: python3-prompt-toolkit liegt in "universe"
if grep -qi '^ID=ubuntu' /etc/os-release 2>/dev/null && command -v add-apt-repository >/dev/null 2>&1; then
    add-apt-repository -y universe >/dev/null 2>&1 || true
fi

if fetch "$URL/Packages" >/dev/null 2>&1; then
    # Paketquelle einrichten → Updates kommen mit "apt upgrade"
    KEYRING=/usr/share/keyrings/hamster-archive-keyring.gpg
    if fetch "$URL/hamster.gpg" > "$KEYRING.tmp" 2>/dev/null && [ -s "$KEYRING.tmp" ]; then
        mv "$KEYRING.tmp" "$KEYRING"; OPT="signed-by=$KEYRING"
    else
        rm -f "$KEYRING.tmp"; OPT="trusted=yes"
    fi
    echo "deb [$OPT] $URL/ ./" > /etc/apt/sources.list.d/hamster.list
    apt-get update || echo "Warnung: apt-get update meldete Fehler (evtl. eine andere Paketquelle) – mache trotzdem weiter." >&2
    apt-get install -y hamster </dev/null
    UPDATE="sudo apt update && sudo apt upgrade"
else
    # Paketquelle (noch) nicht erreichbar → neuestes .deb aus den GitHub-Releases
    echo "Lade hamster aus den GitHub-Releases ..."
    TMP="$(mktemp -d)"
    fetch "$DEB_URL" > "$TMP/hamster_all.deb"
    chmod 755 "$TMP"; chmod 644 "$TMP/hamster_all.deb"
    apt-get update || echo "Warnung: apt-get update meldete Fehler (evtl. eine andere Paketquelle) – mache trotzdem weiter." >&2
    apt-get install -y "$TMP/hamster_all.deb" </dev/null
    rm -rf "$TMP"
    UPDATE="diesen Installationsbefehl nochmal ausführen"
fi

echo
if command -v hamster >/dev/null 2>&1; then
    echo "🐹 hamster $(hamster --version | cut -d' ' -f2) ist installiert! Starten mit:  hamster"
    echo "   Als Standard-Shell:  chsh -s /usr/bin/hamster"
    echo "   Aktualisieren:       $UPDATE"
else
    echo "Installation fehlgeschlagen – der Befehl hamster wurde nicht gefunden." >&2
    exit 1
fi
