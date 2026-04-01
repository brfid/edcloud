#!/usr/bin/env bash
# Link dotfiles configs for edcloud.
# Assumes ~/src/dotfiles is cloned.
set -euo pipefail

DOTFILES="${DOTFILES:-$HOME/src/dotfiles}"

if [ ! -d "$DOTFILES" ]; then
    echo "Error: dotfiles repo not found at $DOTFILES"
    echo "Clone it first: git clone <repo> $DOTFILES"
    exit 1
fi

link() {
    local src="$DOTFILES/$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    ln -sfn "$src" "$dst"
}

# Common
link shell/bashrc           "$HOME/.bashrc"
link shell/aliases          "$HOME/.config/shell/aliases"
link nvim                   "$HOME/.config/nvim"
link tmux/tmux.conf         "$HOME/.tmux.conf"
link git/.gitleaks.toml     "$HOME/.config/git/.gitleaks.toml"
link git/hooks              "$HOME/.config/git/hooks"
link gh/config.yml          "$HOME/.config/gh/config.yml"
link claude/CLAUDE.md       "$HOME/.claude/CLAUDE.md"
link claude/settings.json   "$HOME/.claude/settings.json"
link claude/commands        "$HOME/.claude/commands"
link claude/plugins         "$HOME/.claude/plugins"
link copilot/copilot-instructions.md "$HOME/.copilot/copilot-instructions.md"
link copilot/mcp-config.json         "$HOME/.copilot/mcp-config.json"
link vscode/settings.json   "$HOME/.config/Code/User/settings.json"
link vscode/snippets        "$HOME/.config/Code/User/snippets"
link yazi                   "$HOME/.config/yazi"

# edcloud-specific shell local
link shell/local/edcloud.sh "$HOME/.config/shell/local"

echo "Dotfiles linked for edcloud."
