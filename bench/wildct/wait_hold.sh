#!/usr/bin/env bash
# wait_hold.sh NAME CEILING_S [POLL_S]: wait, on wildct, for the hold bench/wildct/hold.sh NAME to
# end (EXPERIMENTS.md "Wildct amendment R1"). hold.sh writes runs/NAME/pid when it starts and
# runs/NAME/rc last, on every exit. Polls every POLL_S s (default 30).
#   exit 0  rc is 0 (prints "NAME rc=0")        exit 1  rc is not 0 (prints it)
#   exit 2  hold.sh's process is gone and wrote no rc (killed with SIGKILL, or the host rebooted)
#   exit 3  no pid file after 60 s (the hold never started)
#   exit 4  no rc by CEILING_S (the hold is still running: look before waiting again)
set -uo pipefail
name=${1:?usage: wait_hold.sh NAME CEILING_S [POLL_S]} ceil=${2:?usage: wait_hold.sh NAME CEILING_S [POLL_S]} poll=${3:-30}
R=${HOME}/k3dgpu-bench-wildct/runs/${name}
t0=${SECONDS}
while :; do
    if [ -f "${R}/rc" ]; then
        rc=$(cat "${R}/rc")
        echo "${name} rc=${rc}"
        [ "${rc}" = 0 ]
        exit $?
    fi
    if [ -f "${R}/pid" ]; then
        if ! kill -0 "$(cat "${R}/pid")" 2>/dev/null; then
            [ -f "${R}/rc" ] && continue
            echo "${name}: hold.sh (pid $(cat "${R}/pid")) is gone and wrote no rc"
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
