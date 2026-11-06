# shellcheck shell=bash
# gh_retry.sh: gh_retry, for sourcing.
#
#   gh_retry ARGS...   gh ARGS; while it fails with an HTTP 5xx or a connection error
#                      (read from its stderr), again after GH_RETRY_DELAY (default 2) s,
#                      then twice and four times that: at most 4 attempts. Prints the
#                      last attempt's stdout and stderr and returns its exit code. For
#                      reads and other calls that are safe to repeat; a dispatch that
#                      may have landed is confirmed by reading, not by sending it again.

GH_RETRY_TRANSIENT='HTTP 5[0-9][0-9]|Server Error|Bad Gateway|Service Unavailable|Gateway Timeout|connection reset|connection refused|i/o timeout|TLS handshake timeout|unexpected EOF'

gh_retry() {
  local d n rc delay=${GH_RETRY_DELAY:-2}
  d=$(mktemp -d "${TMPDIR:-/tmp}/gh_retry.XXXXXX") || return 1
  for n in 1 2 3 4; do
    command gh "$@" > "$d/out" 2> "$d/err"; rc=$?
    if [ "$rc" -eq 0 ] || [ "$n" -eq 4 ] || ! grep -qiE "$GH_RETRY_TRANSIENT" "$d/err"; then
      cat "$d/out"; cat "$d/err" >&2; rm -rf "$d"
      return "$rc"
    fi
    sleep "$((delay * (1 << (n - 1))))"
  done
}
