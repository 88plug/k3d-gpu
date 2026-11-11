#!/usr/bin/env bash
# prep.sh HOST: the H63 model files on this host (EXPERIMENTS.md "H63 pre-registration", Models;
# "H63 amendment R1"). Preparations are not holds. Every step runs inside the host lock (LOCK,
# flock -w 21600), each step taking it on its own:
#   1. Downloads at the registered revisions into ${G}/models, in H57d's layout: Google's QAT GGUF
#      (gemma-4-26B_q4_0-it.gguf only), the QAT assistant's checkpoint, OpenVINO's int4 IR (OVB);
#      each with a .pinned-revision file. Every file the committed sha256 lists name is checked
#      (bench/h57d/results/2026-10-21/weights.sha256 whole, bench/h59/results/2026-11-10-wildct/
#      weights.sha256's OVB lines: run.py weights-check); a mismatch stops here. The GGUF is also
#      reachable as gguf/<repo>@<commit>, the path Ls's render serves it from (one copy for both
#      llama.cpp arms).
#   2. Ld-mtp's drafter and Od's graph.pbtxt: bench/h57d/convert.sh, unchanged (it takes LOCK).
#   3. Ls's drafter: the conversion Job H59's prep ran, as committed in
#      bench/h59/results/2026-11-10-wildct/prep/drafter (job.json and convert.sh), run once with
#      docker against ${G}/models (bench/h59/run.py docker_argv) under `timeout -k 30 3600`.
#   4. Both drafters against their registered sha256 and size, and Od's graph.pbtxt against
#      H57d's (run.py drafter-check, run.py models); a mismatch stops the sub-row before its
#      smoke, recorded in ${G}/prep/drafter-check.json.
#   5. ${G}/weights.sha256: the sha256 of every file the arms read (the Reproduce header prints it).
#   G  campaign dir (default from the profile);  LOCK  the host lock
set -euo pipefail
HOST=${1:?usage: prep.sh HOST}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
RUN=${HERE}/run.py
[ "${HOST}" = desktop ] || { echo "prep.sh: ${HOST}: H63w's wildct wiring comes with its own amendment" >&2; exit 2; }
pfield() { python3 "${RUN}" profile "${HOST}" | sed -n "s/^$1=//p"; }
G=${G:-$(pfield g)}
G=${G/#\~/${HOME}}
M=${G}/models
LOCKF=${LOCK:?set LOCK to this host\'s lock file}
export HF_HUB_DISABLE_TELEMETRY=1 REPO G
v() { python3 "${RUN}" vars | sed -n "s/^$1=//p"; }
GREPO=$(v GGUF_REPO) GREV=$(v GGUF_REV) GFILE=$(v GGUF_FILE) DREPO=$(v DRAFT_REPO) DREV=$(v DRAFT_REV)
OVB=$(v OVB) OREV=$(v OVB_REV) GDIR=$(v H57D_GGUF_DIR) DSRC=$(v H57D_DRAFT_SRC) LINK=$(v LS_GGUF_LINK)
LSDIR=$(v LS_DRAFT_DIR) LDDRAFT=$(v LDMTP_DRAFT) JOBDIR=$(v LS_DRAFTER_JOB)
[ -n "${GREPO}" ] && [ -n "${JOBDIR}" ] || { echo "prep.sh: run.py vars failed" >&2; exit 1; }
mkdir -p "${M}/gguf" "${G}/prep"
locked() {  # locked CMD...: run CMD inside the host lock
    flock -w 21600 "${LOCKF}" "$@"
}

echo "downloads: waiting for the lock $(date -u +%FT%TZ); / free $(df -P -B1G / | awk 'NR==2{print $4}') GB"
locked sh -ec '
    m=$1; cd "$m"
    echo "lock taken $(date -u +%FT%TZ)"
    hf download "$2" "$4" --revision "$3" --local-dir "$9"
    hf download "$5" --revision "$6" --local-dir "${10}"
    hf download "$7" --revision "$8" --local-dir "$7"
    echo "$2 $3" > "$9/.pinned-revision"
    echo "$5 $6" > "${10}/.pinned-revision"
    echo "$7 $8" > "$7/.pinned-revision"
    python3 "${11}" weights-check "$m"
    echo "downloads checked $(date -u +%FT%TZ)"
' prep "${M}" "${GREPO}" "${GREV}" "${GFILE}" "${DREPO}" "${DREV}" "${OVB}" "${OREV}" "${GDIR}" "${DSRC}" "${RUN}"
ln -sfnT "../${GDIR}" "${M}/${LINK}"

# Ld-mtp's drafter and Od's graph.pbtxt (H57d's convert.sh takes LOCK itself).
LOCK=${LOCKF} G=${G} bash "${REPO}/bench/h57d/convert.sh"

# Ls's drafter: the committed Job, run once.
if [ ! -f "${M}/${LSDIR}/manifest.json" ]; then
    J=${JOBDIR}
    mapfile -t ARGV < <(python3 "${RUN}" docker-argv "${J}/job.json" "${M}" "${J}")
    echo "Ls drafter: waiting for the lock $(date -u +%FT%TZ)"
    locked sh -c 'echo "lock taken $(date -u +%FT%TZ); converting the drafter"; exec timeout -k 30 3600 "$@"' conv "${ARGV[@]}"
    cp "${J}/job.json" "${M}/${LSDIR}/prep-job.json"
fi
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print("Ls drafter:", m["source"], m["quantize"], m["sha256"], m["bytes"])' "${M}/${LSDIR}/manifest.json"

if ! python3 "${RUN}" drafter-check "${G}" > "${G}/prep/drafter-check.json"; then
    cat "${G}/prep/drafter-check.json"
    echo "prep.sh: a drafter is not the registered build: the sub-row stops before its smoke" >&2
    exit 3
fi
cat "${G}/prep/drafter-check.json"
python3 "${RUN}" models "${G}"
( cd "${M}" && find "${GDIR}/${GFILE}" "${OVB}" "${DSRC}" "${LDDRAFT}" \
        "${LSDIR}/model.gguf" -type f ! -path '*/.cache/*' ! -name .pinned-revision -print0 \
    | sort -z | xargs -0 sha256sum ) > "${G}/weights.sha256"
echo "done $(date -u +%FT%TZ); / free $(df -P -B1G / | awk 'NR==2{print $4}') GB"
