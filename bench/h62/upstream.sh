#!/usr/bin/env bash
# upstream.sh: H62's re-run trigger (EXPERIMENTS.md "H62 pre-registration", "Re-run trigger").
# Reads the tags of EAS and EAT with `git ls-remote` and prints "fired: none", or the release tags
# that fired it: an EAS v1.2.x release (not a release candidate) or an EAT 2.x release.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
d=$(mktemp -d)
trap 'rm -rf "${d}"' EXIT
git ls-remote --tags https://github.com/intel/enterprise-ai-solutions.git > "${d}/eas"
git ls-remote --tags https://github.com/intel/enterprise-agent-toolkit.git > "${d}/eat"
python3 "${HERE}/h62.py" trigger "${d}/eas" "${d}/eat"
