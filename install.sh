#!/bin/sh

set -eu

REPOSITORY="ziward-inc/custom-font-wizard"
DEFAULT_ARCHIVE_URL="https://github.com/${REPOSITORY}/releases/latest/download/custom-font-wizard-source.tar.gz"
ARCHIVE_URL="${CUSTOM_FONT_WIZARD_ARCHIVE_URL:-$DEFAULT_ARCHIVE_URL}"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
INSTALL_DIR="${CUSTOM_FONT_WIZARD_HOME:-${DATA_HOME}/custom-font-wizard}"
BIN_DIR="${CUSTOM_FONT_WIZARD_BIN_DIR:-${HOME}/.local/bin}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'error: required command not found: %s\n' "$1" >&2
        exit 1
    fi
}

require_command cargo
require_command uv
require_command tar
require_command mktemp

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/custom-font-wizard-install.XXXXXX")"
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

ARCHIVE_PATH="${TEMP_DIR}/custom-font-wizard-source.tar.gz"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE_PATH"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$ARCHIVE_PATH" "$ARCHIVE_URL"
else
    printf 'error: curl or wget is required\n' >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
tar -xzf "$ARCHIVE_PATH" -C "$INSTALL_DIR" --strip-components=1

uv sync --project "$INSTALL_DIR" --locked --no-dev
cargo build --manifest-path "$INSTALL_DIR/Cargo.toml" --release --locked
cp "$INSTALL_DIR/target/release/custom-font-wizard" "$BIN_DIR/custom-font-wizard"
chmod 755 "$BIN_DIR/custom-font-wizard"

printf 'Installed custom-font-wizard to %s\n' "$BIN_DIR/custom-font-wizard"
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) printf 'Add %s to PATH before running custom-font-wizard.\n' "$BIN_DIR" ;;
esac
