#!/usr/bin/env bash
# Adds "Bud Decision Studio" to your desktop's application menu (GNOME and most Linux desktops). Remove it with:
#   rm ~/.local/share/applications/basal-studio.desktop
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/basal-studio.desktop <<DESKTOP
[Desktop Entry]
Type=Application
Name=Bud Decision Studio
Comment=Run and try decision models on this GPU
Exec=$HERE/scripts/open.sh
Icon=$HERE/ui/brand/app-icon.png
Terminal=false
Categories=Development;Science;
DESKTOP
echo "Added Bud Decision Studio to your applications menu."
