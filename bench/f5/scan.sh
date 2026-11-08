#!/usr/bin/env bash
# Secret scan for an EAS run (F3 step 7, reused unchanged by F5). Prints counts only, never a value.
#   scan.sh read  <vals dir> <kubeconfig>        read the values from the cluster into <vals dir> (0700)
#   scan.sh count <vals dir> <label> <out> <file|dir>...
#        count each value, raw and base64, with fixed-string grep -c over the files (gzip
#        files through zcat); print one line per value and form; delete each file with a
#        hit after counting and name it.
set -uo pipefail

mode=$1 vd=$2
shift 2
case "${mode}" in
read)
    kcfg=$1
    ( umask 077; mkdir -p "${vd}" )
    k() { KUBECONFIG="${kcfg}" kubectl "$@"; }
    k -n litellm get secret litellm-master-key -o jsonpath='{.data.master_key}' 2>/dev/null | base64 -d > "${vd}/litellm-master-key" 2>/dev/null
    k -n agent-sandbox-system get secret sandbox-router-auth -o jsonpath='{.data.ROUTER_AUTH_TOKEN}' 2>/dev/null | base64 -d > "${vd}/sandbox-router-auth" 2>/dev/null
    # keycloak-admin-secret, if the install made one, in any namespace: its password key
    # (F3 amendment R2: the username key is an account name, not a secret).
    k get secret -A --field-selector metadata.name=keycloak-admin-secret -o json 2>/dev/null \
        | python3 -c '
import base64, json, os, sys
vd = sys.argv[1]
for s in json.load(sys.stdin).get("items", []):
    for key, val in (s.get("data") or {}).items():
        name = "keycloak-admin-secret.%s.%s" % (s["metadata"]["namespace"], key)
        if key != "password":
            print("not counted: %s (an account name, amendment R2)" % name)
            continue
        with open(os.path.join(vd, name), "wb") as f:
            f.write(base64.b64decode(val))
' "${vd}"
    if [ -n "${HF_TOKEN:-}" ]; then printf '%s' "${HF_TOKEN}" > "${vd}/HF_TOKEN"; fi
    for f in "${vd}"/*; do
        [ -e "${f}" ] || continue
        if [ -s "${f}" ]; then echo "read: $(basename "${f}") ($(wc -c < "${f}") bytes)"; else echo "read: $(basename "${f}") EMPTY (absent)"; rm -f "${f}"; fi
    done
    ;;
count)
    label=$1   # $2, the run's out dir, names the record only
    shift 2
    files=()
    # Step 7's own log is being written while it counts, and names the Secrets only.
    while IFS= read -r -d '' f; do files+=("${f}"); done < <(find "$@" -type f ! -name 7-scan.log -print0 2>/dev/null)
    echo "# scan ${label}: ${#files[@]} files"
    declare -A hit=()
    total=0
    for vf in "${vd}"/*; do
        [ -s "${vf}" ] || continue
        name=$(basename "${vf}")
        for form in raw base64; do
            n=0
            for f in "${files[@]}"; do
                if [ "${form}" = raw ]; then pat=$(cat "${vf}"); else pat=$(base64 -w0 < "${vf}"); fi
                case "${f}" in
                    *.gz) c=$(zcat -f "${f}" 2>/dev/null | grep -c -F -f <(printf '%s\n' "${pat}")) ;;
                    *)    c=$(grep -c -F -f <(printf '%s\n' "${pat}") "${f}" 2>/dev/null) ;;
                esac
                c=${c:-0}
                if [ "${c}" -gt 0 ]; then hit["${f}"]=1; fi
                # ps samples: name the processes with a hit, the value replaced by <VALUE>.
                if [ "${c}" -gt 0 ] && [ "${f%.gz}" != "${f}" ]; then
                    zcat -f "${f}" 2>/dev/null | python3 -c '
import base64, collections, os, sys
v = open(sys.argv[1], "rb").read()
pat = (base64.b64encode(v) if sys.argv[2] == "base64" else v).decode()
seen = collections.Counter()
for line in sys.stdin:
    if pat in line:
        t = line.replace(pat, "<VALUE>").split()
        seen[" ".join([os.path.basename(t[0])] + [x[:60] for x in t[1:6]])] += 1
for k, n in seen.most_common(8):
    print("  %s %s %s hit process x%d: %s" % (sys.argv[3], sys.argv[4], sys.argv[2], n, k))
' "${vf}" "${form}" "${label}" "${name}"
                fi
                n=$(( n + c ))
            done
            echo "${label} ${name} ${form}: ${n}"
            total=$(( total + n ))
        done
    done
    echo "${label} total: ${total}"
    for f in "${!hit[@]}"; do echo "${label} hit file deleted: ${f}"; rm -f "${f}"; done
    ;;
*) echo "usage: scan.sh read|count ..." >&2; exit 2 ;;
esac
