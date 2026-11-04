#!/bin/sh
# One-line installer for swe on Linux:
#
#   curl -fsSL https://github.com/c-wenlong/swe/releases/latest/download/install.sh | sh
#   ... | sh -s -- --hub        # also runs `swe remote setup --apply`
#
# Everything lives inside functions and `main` runs last, so a truncated
# download never executes a half-read script.  No sudo, no shell rc edits.
set -eu

REPO="https://github.com/c-wenlong/swe"

say() {
    printf '%s\n' "$*"
}

err() {
    printf '%s\n' "swe install: $*" >&2
}

die() {
    err "$*"
    exit 1
}

usage() {
    cat <<'EOF'
Usage: install.sh [--hub] [--version=vX.Y.Z] [--wheel=PATH] [--help]

Installs the latest released swe on Linux.  Prefers uv, falls back to a
venv under ${XDG_DATA_HOME:-~/.local/share}/swe, and bootstraps uv when
neither is available.  --hub also runs `swe remote setup --apply`.
EOF
}

need_curl() {
    command -v curl >/dev/null 2>&1 || die "curl is required (or install a local wheel with --wheel=PATH)"
}

# The latest release tag, from the redirect URL of releases/latest.  The
# GitHub API is deliberately not used: it rate-limits anonymous calls.
latest_tag() {
    url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO/releases/latest" </dev/null)" \
        || die "could not resolve the latest release"
    tag="${url##*/}"
    case "$tag" in
        v[0-9]*) printf '%s\n' "$tag" ;;
        *) die "unexpected latest release URL: $url" ;;
    esac
}

download() {
    # $1: URL, $2: destination path.  Fails on HTTP errors.
    curl -fsSL -o "$2" "$1" </dev/null
}

verify_checksum() {
    # $1: wheel file, $2: wheel filename, $3: tag.
    sums_url="$REPO/releases/download/$3/SHA256SUMS"
    sums_file="$TMPDIR_INSTALL/SHA256SUMS"
    if ! download "$sums_url" "$sums_file"; then
        # Releases cut before this installer existed carry no SHA256SUMS.
        say "swe install: no SHA256SUMS on release $3, skipping checksum verification"
        return 0
    fi
    expected="$(awk -v name="$2" '$2 == name {print $1}' "$sums_file")"
    if [ -z "$expected" ]; then
        die "SHA256SUMS on release $3 has no line for $2"
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$1" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$1" | awk '{print $1}')"
    else
        die "SHA256SUMS is present but neither sha256sum nor shasum is on PATH"
    fi
    [ "$actual" = "$expected" ] || die "checksum mismatch for $2"
}

install_with_uv() {
    # $1: uv binary (path or name on PATH), $2: wheel path.
    "$1" tool install --force --python '>=3.11' "$2" </dev/null || return 1
    BIN_DIR="$("$1" tool dir --bin </dev/null)" || return 1
    [ -n "$BIN_DIR" ] || return 1
    return 0
}

try_venv() {
    # $1: wheel path.  Returns 0 when the venv install worked.
    command -v python3 >/dev/null 2>&1 || return 1
    python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' </dev/null || return 1
    venv_dir="${XDG_DATA_HOME:-$HOME/.local/share}/swe/venv"
    rm -rf "$venv_dir"
    if ! python3 -m venv "$venv_dir" </dev/null 2>/dev/null; then
        # e.g. Debian/Ubuntu without the python3-venv package: remove only
        # the directory this run tried to create, then fall through to uv.
        rm -rf "$venv_dir"
        return 1
    fi
    if ! "$venv_dir/bin/pip" install --upgrade "$1" </dev/null; then
        rm -rf "$venv_dir"
        return 1
    fi
    BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
    mkdir -p "$BIN_DIR"
    ln -sf "$venv_dir/bin/swe" "$BIN_DIR/swe"
    return 0
}

bootstrap_uv() {
    mkdir -p "$BIN_DIR"
    say "Installing uv into $BIN_DIR to manage swe's Python"
    curl -LsSf https://astral.sh/uv/install.sh </dev/null \
        | env UV_INSTALL_DIR="$BIN_DIR" UV_NO_MODIFY_PATH=1 sh
    [ -x "$BIN_DIR/uv" ] || die "could not install uv from astral.sh"
    install_with_uv "$BIN_DIR/uv" "$1"
}

note_existing_swe() {
    # Warn when `swe` on PATH resolves somewhere other than the new install.
    existing="$(command -v swe 2>/dev/null || true)"
    if [ -n "$existing" ] && [ "$existing" != "$BIN_DIR/swe" ]; then
        say "note: 'swe' already on PATH at $existing; the new one is $BIN_DIR/swe"
    fi
}

path_hint() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) return 0 ;;
    esac
    say "add $BIN_DIR to your PATH:"
    say "  export PATH=\"$BIN_DIR:\$PATH\""
}

main() {
    hub=0
    version=""
    wheel=""
    for arg in "$@"; do
        case "$arg" in
            --hub) hub=1 ;;
            --version=*) version="${arg#--version=}" ;;
            --wheel=*) wheel="${arg#--wheel=}" ;;
            --help) usage; exit 0 ;;
            *) usage >&2; err "unknown option: $arg"; exit 2 ;;
        esac
    done

    case "$(uname -s 2>/dev/null || echo unknown)" in
        Linux) ;;
        Darwin)
            err "On macOS use Homebrew: brew install c-wenlong/swe/swe (CLI) or brew install --cask c-wenlong/swe/swe (app)"
            exit 1
            ;;
        *) die "this installer supports Linux only" ;;
    esac

    if [ -n "$wheel" ]; then
        [ -f "$wheel" ] || die "wheel not found: $wheel"
        wheel_name="${wheel##*/}"
    else
        need_curl
        if [ -n "$version" ]; then
            tag="$version"
        else
            tag="$(latest_tag)"
        fi
        wheel_name="swe-${tag#v}-py3-none-any.whl"
        TMPDIR_INSTALL="$(mktemp -d)"
        trap 'rm -rf "$TMPDIR_INSTALL"' EXIT
        wheel="$TMPDIR_INSTALL/$wheel_name"
        say "Downloading $wheel_name ($tag)"
        download "$REPO/releases/download/$tag/$wheel_name" "$wheel" \
            || die "download failed: $REPO/releases/download/$tag/$wheel_name"
        verify_checksum "$wheel" "$wheel_name" "$tag"
    fi

    BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
    if command -v uv >/dev/null 2>&1; then
        install_with_uv uv "$wheel" || die "uv tool install failed"
    elif try_venv "$wheel"; then
        :
    else
        bootstrap_uv "$wheel" || die "could not install swe"
    fi

    note_existing_swe

    say "installed: $("$BIN_DIR/swe" --version </dev/null 2>&1 || echo "swe at $BIN_DIR/swe")"
    path_hint
    say "next: swe setup"
    say "for the web app and phone remote: swe remote setup --apply"
    if [ "$hub" -eq 0 ]; then
        say "(or rerun this script with --hub)"
    else
        if PATH="$BIN_DIR:$PATH" "$BIN_DIR/swe" remote setup --apply </dev/null; then
            say "remote setup done"
        else
            say "finish the steps above, then run: swe remote setup --apply"
        fi
    fi
}

main "$@"
