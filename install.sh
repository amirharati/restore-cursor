#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
python_command="${RESTORE_CURSOR_PYTHON:-python3}"
install_root="${RESTORE_CURSOR_INSTALL_ROOT:-$HOME/.local/share/restore-cursor}"
command_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
command_path="$command_dir/restore-cursor"
legacy_launcher="$project_root/bin/restore-cursor"

if ! command -v "$python_command" >/dev/null 2>&1; then
    echo "install.sh: Python command not found: $python_command" >&2
    exit 1
fi
python_path="$(command -v "$python_command")"

if ! "$python_path" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "install.sh: Python 3.10 or newer is required." >&2
    exit 1
fi

if [[ -z "$install_root" || "$install_root" == "/" || "$install_root" == "$HOME" ]]; then
    echo "install.sh: Refusing unsafe install root: $install_root" >&2
    exit 1
fi

if [[ -e "$command_path" || -L "$command_path" ]]; then
    managed=false
    if [[ -L "$command_path" && "$(readlink "$command_path")" == "$legacy_launcher" ]]; then
        managed=true
    elif [[ -f "$command_path" ]] && grep -q '^# managed-by: restore-cursor install.sh$' "$command_path"; then
        managed=true
    fi
    if [[ "$managed" != true ]]; then
        echo "install.sh: Refusing to replace unrelated command: $command_path" >&2
        exit 1
    fi
fi

install_parent="$(dirname "$install_root")"
mkdir -p "$install_parent" "$command_dir"
stage="$(mktemp -d "$install_parent/.restore-cursor.install.XXXXXX")"
launcher_temp="$(mktemp "$command_dir/.restore-cursor.XXXXXX")"
previous=""

cleanup() {
    [[ -n "${stage:-}" && -d "$stage" ]] && rm -rf "$stage"
    [[ -n "${launcher_temp:-}" && -e "$launcher_temp" ]] && rm -f "$launcher_temp"
    return 0
}
trap cleanup EXIT

mkdir -p "$stage/src"
cp -R "$project_root/src/restore_cursor" "$stage/src/"
printf 'source=%s\ninstalled_at=%s\n' \
    "$project_root" \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    > "$stage/INSTALL-METADATA"

{
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' '# managed-by: restore-cursor install.sh'
    printf '%s\n' 'set -euo pipefail'
    printf 'install_root=%q\n' "$install_root"
    printf 'python_command=%q\n' "$python_path"
    printf '%s\n' 'export PYTHONPATH="$install_root/src${PYTHONPATH:+:$PYTHONPATH}"'
    printf '%s\n' 'exec "$python_command" -m restore_cursor.cli "$@"'
} > "$launcher_temp"
chmod 755 "$launcher_temp"

if [[ -e "$install_root" ]]; then
    previous="$install_parent/.restore-cursor.previous.$$"
    mv "$install_root" "$previous"
fi

if ! mv "$stage" "$install_root"; then
    [[ -n "$previous" && -e "$previous" ]] && mv "$previous" "$install_root"
    exit 1
fi
stage=""

if ! mv -f "$launcher_temp" "$command_path"; then
    rm -rf "$install_root"
    [[ -n "$previous" && -e "$previous" ]] && mv "$previous" "$install_root"
    exit 1
fi
launcher_temp=""

[[ -n "$previous" && -e "$previous" ]] && rm -rf "$previous"

echo "Installed restore-cursor runtime: $install_root"
echo "Installed command: $command_path"
case ":$PATH:" in
    *":$command_dir:"*) ;;
    *) echo "Add $command_dir to PATH before running restore-cursor." ;;
esac
