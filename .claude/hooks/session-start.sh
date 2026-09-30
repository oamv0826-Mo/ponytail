#!/bin/bash
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

claude plugin marketplace add anthropics/claude-plugins-official || true
for p in superpowers claude-code-setup frontend-design security-guidance playwright github; do
  claude plugin install "$p@claude-plugins-official" || true
done
