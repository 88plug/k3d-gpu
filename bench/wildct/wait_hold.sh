#!/usr/bin/env bash
# wait_hold.sh NAME CEILING_S [POLL_S]: wait, on wildct, for the hold bench/wildct/hold.sh NAME to
# end (EXPERIMENTS.md "Wildct amendment R1"). hold.sh writes runs/NAME/pid when it starts and
# runs/NAME/rc last, on every exit. Polls every POLL_S s (default 30). A pid or rc file that does
# not hold a number (caught while being written) is read as not written yet.
#   exit 0  rc is 0 (prints "NAME rc=0")        exit 1  rc is not 0 (prints it)
#   exit 2  hold.sh's process is gone and wrote no rc (killed with SIGKILL, or the host rebooted)
#   exit 3  no pid file after 60 s (the hold never started)
#   exit 4  no rc by CEILING_S (the hold is still running: look before waiting again)
set -uo pipefail
name=${1:?usage: wait_hold.sh NAME CEILING_S [POLL_S]} ceil=${2:?usage: wait_hold.sh NAME CEILING_S [POLL_S]} poll=${3:-30}
R=${HOME}/k3dgpu-bench-wildct/runs/${name}
num() { local v; v=$(cat "$1" 2>/dev/null) && [[ "${v}" =~ ^[0-9]+$ ]] && echo "${v}"; }
t0=${SECONDS}
while :; do
    if rc=$(num "${R}/rc"); then
        echo "${name} rc=${rc}"
        [ "${rc}" = 0 ]
        exit $?
    fi
    if pid=$(num "${R}/pid"); then
        if ! kill -0 "${pid}" 2>/dev/null; then
            # rc is written before the process ends: read it once more before calling the hold dead.
            num "${R}/rc" > /dev/null && continue
            echo "${name}: hold.sh (pid ${pid}) is gone and wrote no rc"
            exit 2
        fi
    elif [ $(( SECONDS - t0 )) -ge 60 ]; then
        echo "${name}: no ${R}/pid after 60 s: the hold never started"
        exit 3
    fi
    if [ $(( SECONDS - t0 )) -ge "${ceil}" ]; then
        echo "${name}: no rc after ${ceil} s; still running as pid $(cat "${R}/pid" 2>/dev/null || echo '?')"
        exit 4
    fi
    sleep "${poll}"
done
