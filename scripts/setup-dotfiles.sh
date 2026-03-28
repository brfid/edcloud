#!/usr/bin/env bash
# Link dotfiles configs for edcloud via stow.
# Assumes ~/src/dotfiles is cloned.
set -euo pipefail

DOTFILES="${DOTFILES:-$HOME/src/dotfiles}"

if [ ! -d "$DOTFILES" ]; then
    echo "Error: dotfiles repo not found at $DOTFILES"
    echo "Clone it first: git clone <repo> $DOTFILES"
    exit 1
fi

cd "$DOTFILES"
stow --target="$HOME" bash shell nvim gh claude vscode tmux systemd-user git

# Profile-specific shell local
ln -sfn "$DOTFILES/shell/.config/shell/local.d/edcloud.sh" \
    "$HOME/.config/shell/local"

echo "Dotfiles linked for edcloud."
