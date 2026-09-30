#!/bin/sh
# Installs Bud Decision Studio, the desktop app, from the latest GitHub release.
#
#   curl -fsSL https://raw.githubusercontent.com/BudEcosystem/Bud-Decision-Engine/main/get.sh | sh
#
# macOS (Apple Silicon): copies the app into /Applications (or ~/Applications) and opens it.
# Linux: installs the .deb or .rpm package when possible (asks for your password once), otherwise the AppImage for
#        your user only. Either way the app appears in your applications menu. Options:
#          sh get.sh --appimage      never use sudo; install the AppImage into ~/.local/bin
#          sh get.sh --version v0.1.0
# Windows: use get.ps1 instead.
set -eu

REPO="BudEcosystem/Bud-Decision-Engine"
NAME="Bud Decision Studio"
VERSION="latest"
APPIMAGE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --appimage) APPIMAGE_ONLY=1 ;;
    --version) shift; VERSION="$1" ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
  esac
  shift
done

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "this installer needs '$1'"; }
need curl

os=$(uname -s)
arch=$(uname -m)
case "$arch" in x86_64|amd64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; *) die "unsupported processor: $arch" ;; esac

if [ "$VERSION" = latest ]; then api="https://api.github.com/repos/$REPO/releases/latest"; else api="https://api.github.com/repos/$REPO/releases/tags/$VERSION"; fi
say "Finding the $VERSION release of $NAME"
assets=$(curl -fsSL "$api" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(https[^"]*\)"/\1/') || die "could not reach GitHub"
[ -n "$assets" ] || die "no downloads found in the release"

pick() { printf '%s\n' "$assets" | grep -E "$1" | head -n 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fetch() { say "Downloading $(basename "$1" | sed 's/%20/ /g')"; curl -fL --progress-bar -o "$tmp/$2" "$1"; }

if [ "$os" = Darwin ]; then
  [ "$arch" = aarch64 ] || die "macOS builds are for Apple Silicon (M1 and newer); Intel Macs are not supported by PyTorch"
  url=$(pick '\.dmg$') ; [ -n "$url" ] || die "no macOS download in this release"
  fetch "$url" app.dmg
  mnt=$(hdiutil attach -nobrowse -readonly "$tmp/app.dmg" | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -n 1)
  src=$(find "$mnt" -maxdepth 1 -name '*.app' | head -n 1)
  dest=/Applications
  [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
  rm -rf "$dest/$NAME.app"
  ditto "$src" "$dest/$NAME.app"
  hdiutil detach -quiet "$mnt" || true
  say "Installed in $dest. Opening $NAME"
  open "$dest/$NAME.app"
  exit 0
fi

[ "$os" = Linux ] || die "unsupported system: $os (on Windows, use get.ps1)"
debarch=$([ "$arch" = x86_64 ] && echo amd64 || echo arm64)

has_tty() { (exec </dev/tty) 2>/dev/null; }   # a terminal to ask for the password on, even when piped from curl
can_sudo() { [ "$(id -u)" = 0 ] || { command -v sudo >/dev/null 2>&1 && { sudo -n true 2>/dev/null || has_tty; }; }; }
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@" </dev/tty; fi; }

if [ "$APPIMAGE_ONLY" = 0 ] && command -v apt-get >/dev/null 2>&1 && can_sudo; then
  url=$(pick "_${debarch}\\.deb$")
  if [ -n "$url" ]; then
    fetch "$url" app.deb
    say "Installing the package (your password may be asked once)"
    if as_root apt-get install -y "$tmp/app.deb"; then
      say "Done. Open $NAME from your applications menu, or run: bud-decision-studio"
      exit 0
    fi
    say "The package could not be installed; installing the AppImage for your user instead"
  fi
fi
if [ "$APPIMAGE_ONLY" = 0 ] && { command -v dnf >/dev/null 2>&1 || command -v zypper >/dev/null 2>&1; } && can_sudo; then
  url=$(pick "\\.${arch}\\.rpm$")
  if [ -n "$url" ]; then
    fetch "$url" app.rpm
    say "Installing the package (your password may be asked once)"
    if { command -v dnf >/dev/null 2>&1 && as_root dnf install -y "$tmp/app.rpm"; } || \
       { command -v zypper >/dev/null 2>&1 && as_root zypper --non-interactive install --allow-unsigned-rpm "$tmp/app.rpm"; }; then
      say "Done. Open $NAME from your applications menu, or run: bud-decision-studio"
      exit 0
    fi
    say "The package could not be installed; installing the AppImage for your user instead"
  fi
fi

# No package manager or no sudo: the AppImage, for this user only.
url=$(pick "_(${arch}|${debarch})\\.AppImage$") ; [ -n "$url" ] || die "no AppImage for $arch in this release"
fetch "$url" app.AppImage
bin="$HOME/.local/bin"
mkdir -p "$bin"
install -m 755 "$tmp/app.AppImage" "$bin/bud-decision-studio.AppImage"
say "Installed $bin/bud-decision-studio.AppImage"
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  say "Opening $NAME; it adds itself to your applications menu"
  nohup "$bin/bud-decision-studio.AppImage" >/dev/null 2>&1 &
else
  say "Run it from a desktop session once: $bin/bud-decision-studio.AppImage (it then adds itself to your applications menu)"
fi
