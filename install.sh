#!/usr/bin/env bash
# Installiert klar nach ~/.local/share/klar und legt den Befehl "klar" in ~/.local/bin an.
set -e
DIR="$HOME/.local/share/klar"
SRC="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR" "$HOME/.local/bin"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet --upgrade prompt_toolkit
cp "$SRC/klar.py" "$DIR/klar.py"
cat > "$HOME/.local/bin/klar" <<EOS
#!/usr/bin/env bash
exec "$DIR/venv/bin/python" "$DIR/klar.py" "\$@"
EOS
chmod +x "$HOME/.local/bin/klar"
echo "Fertig! Starte mit:  klar"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Hinweis: ~/.local/bin ist nicht im PATH. Füge in ~/.bashrc hinzu:  export PATH=\"\$HOME/.local/bin:\$PATH\"";; esac
echo "Als Standard-Shell (optional):  echo ~/.local/bin/klar | sudo tee -a /etc/shells && chsh -s ~/.local/bin/klar"
