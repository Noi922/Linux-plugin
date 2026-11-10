#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Baut hamster aus diesem Quellcode als .deb und installiert es mit apt (Ubuntu & Debian).
# Normalerweise brauchst du das nicht – nimm die Paketquelle (siehe README).
set -euo pipefail
cd "$(dirname "$0")"
command -v dpkg-deb >/dev/null || { echo "dpkg-deb fehlt (sudo apt install dpkg)"; exit 1; }
DEB="$(packaging/build-deb.sh "$(cat VERSION).0" "$(mktemp -d)")"
sudo apt-get install -y "$DEB"
echo "🐹 Fertig! Starte mit: hamster"
