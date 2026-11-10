#!/bin/sh
# Richtet die hamster-Paketquelle ein und installiert hamster (Ubuntu & Debian).
#   curl -fsSL https://noi922.github.io/Linux-plugin/setup.sh | sudo sh
#   wget -qO-  https://noi922.github.io/Linux-plugin/setup.sh | sudo sh
set -e
URL="${HAMSTER_REPO_URL:-https://noi922.github.io/Linux-plugin}"
if [ "$(id -u)" != 0 ]; then
    echo "Bitte mit sudo ausführen:  curl -fsSL $URL/setup.sh | sudo sh" >&2
    exit 1
fi
fetch() { if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"; else wget -qO- "$1"; fi; }

KEYRING=/usr/share/keyrings/hamster-archive-keyring.gpg
if fetch "$URL/hamster.gpg" > "$KEYRING.tmp" 2>/dev/null && [ -s "$KEYRING.tmp" ]; then
    mv "$KEYRING.tmp" "$KEYRING"; OPT="signed-by=$KEYRING"
else
    rm -f "$KEYRING.tmp"; OPT="trusted=yes"
fi
echo "deb [$OPT] $URL/ ./" > /etc/apt/sources.list.d/hamster.list

# Ubuntu: python3-prompt-toolkit liegt in "universe"
if grep -qi '^ID=ubuntu' /etc/os-release 2>/dev/null && command -v add-apt-repository >/dev/null 2>&1; then
    add-apt-repository -y universe >/dev/null 2>&1 || true
fi

apt-get update
apt-get install -y hamster </dev/null
echo
echo "🐹 hamster ist installiert! Starten mit:  hamster"
echo "   Als Standard-Shell:  chsh -s /usr/bin/hamster"
echo "   Updates kommen ab jetzt mit:  sudo apt update && sudo apt upgrade"
