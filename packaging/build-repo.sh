#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Erzeugt aus den .deb-Dateien ein apt-Repository: packaging/build-repo.sh DEB-ORDNER ZIELORDNER
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$(cd "$1" && pwd)"; mkdir -p "$2"; DST="$(cd "$2" && pwd)"
cp "$SRC"/*.deb "$DST/"
cp "$ROOT/packaging/setup.sh" "$ROOT/packaging/index.html" "$DST/"
cd "$DST"
dpkg-scanpackages --multiversion . /dev/null > Packages 2>/dev/null
gzip -9kf Packages
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=hamster -o APT::FTPArchive::Release::Label=hamster \
  -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable \
  -o "APT::FTPArchive::Release::Architectures=all amd64 arm64 armhf i386" \
  -o "APT::FTPArchive::Release::Description=hamster shell" release . > Release
# Optional signieren, wenn ein GPG-Schlüssel importiert ist (HAMSTER_GPG_KEY_ID gesetzt)
if [ -n "${HAMSTER_GPG_KEY_ID:-}" ]; then
  gpg --batch --yes --default-key "$HAMSTER_GPG_KEY_ID" --clearsign -o InRelease Release
  gpg --batch --yes --default-key "$HAMSTER_GPG_KEY_ID" -abs -o Release.gpg Release
  gpg --batch --yes --export "$HAMSTER_GPG_KEY_ID" > hamster.gpg
fi
echo "Repository in $DST"
