#!/usr/bin/env bash
set -euo pipefail

install_root="${RESTORE_CURSOR_INSTALL_ROOT:-$HOME/.local/share/restore-cursor}"
command_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
command_path="$command_dir/restore-cursor"

if [[ -z "$install_root" || "$install_root" == "/" || "$install_root" == "$HOME" ]]; then
    echo "uninstall.sh: Refusing unsafe install root: $install_root" >&2
    exit 1
fi

if [[ -e "$command_path" || -L "$command_path" ]]; then
    if [[ -f "$command_path" ]] && grep -q '^# managed-by: restore-cursor install.sh$' "$command_path"; then
        rm -f "$command_path"
        echo "Removed command: $command_path"
    else
        echo "uninstall.sh: Refusing to remove unrelated command: $command_path" >&2
        exit 1
    fi
fi

if [[ -d "$install_root" ]]; then
    rm -rf "$install_root"
    echo "Removed runtime: $install_root"
fi
