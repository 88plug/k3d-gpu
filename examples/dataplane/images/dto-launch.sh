#!/bin/sh
# Preload Intel DTO (DSA Transparent Offload) into a command, pointed at the DSA
# work queues the device plugin mounted into this container.
#
# The DSA plugin mounts only the allocated queue(s) under /dev/dsa and sets no
# environment variable naming them, and libdto is built without accel-config
# auto-discovery, so DTO_WQ_LIST is derived here from /dev/dsa/wq*.
# With no queue mounted the command runs without DTO (plain CPU memcpy).
#
# Usage: dto-launch <command> [args...]
set -eu

LIBDTO=${LIBDTO:-/usr/local/lib/libdto.so}

if [ -z "${DTO_WQ_LIST:-}" ]; then
    wqs=""
    for dev in /dev/dsa/wq*; do
        [ -e "${dev}" ] || continue
        wqs="${wqs:+${wqs};}$(basename "${dev}")"
    done
    if [ -n "${wqs}" ]; then
        DTO_WQ_LIST=${wqs}
        export DTO_WQ_LIST
    fi
fi

if [ -n "${DTO_WQ_LIST:-}" ] && [ -f "${LIBDTO}" ]; then
    echo "dto-launch: LD_PRELOAD=${LIBDTO} DTO_WQ_LIST=${DTO_WQ_LIST}" >&2
    LD_PRELOAD="${LIBDTO}${LD_PRELOAD:+:${LD_PRELOAD}}"
    export LD_PRELOAD
else
    echo "dto-launch: no /dev/dsa/wq* in this container; running without DTO" >&2
fi

exec "$@"
