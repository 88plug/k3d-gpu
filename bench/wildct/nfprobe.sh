#!/usr/bin/env bash
# nfprobe.sh IMAGE: which iptables extensions and ipset set types the running kernel serves to the
# node image's own iptables-nft (k3s's mode on wildct) and ipset (wildct amendment R3). Runs one
# throwaway container (k3dgpu-w-nfprobe) from IMAGE in its own network namespace (--network none,
# only NET_ADMIN and NET_RAW, CPUs 20-23, at most 120 s) that adds one rule per extension to a
# scratch chain and creates one set per type; the namespace and its rules go with the container.
# With kernel.modprobe empty the kernel loads nothing for it; with it set, the kernel loads what
# the rules ask for, as it would for k3s. Prints one ok/FAIL line per check, then
# `NFPROBE clean` (exit 0) or `NFPROBE missing: <extension or set type>...` (exit 1).
# smoke3 (2026-10-29): k3s's network policy controller stopped on the first, comment.
set -uo pipefail
IMAGE=${1:?usage: nfprobe.sh IMAGE}
out=$(timeout -k 10 120 docker run --rm -i --name k3dgpu-w-nfprobe --network none --cap-drop ALL \
    --cap-add NET_ADMIN --cap-add NET_RAW --cpuset-cpus 20-23 --entrypoint /bin/sh "${IMAGE}" -s <<'EOF'
A=/bin/aux; I=$A/iptables-nft; I6=$A/ip6tables-nft; S=/bin/ipset
command -v ipset > /dev/null && S=$(command -v ipset)
t() { b=$1; tbl=$2; name=$3; shift 3
      if o=$($b -w -t "$tbl" -A K3DGPU_PROBE "$@" 2>&1); then echo "ok ${b##*/} $tbl $name"
      else echo "FAIL ${b##*/} $tbl $name :: $(echo "$o" | head -1)"; fi; }
for b in $I $I6; do for tbl in filter nat; do $b -w -t $tbl -N K3DGPU_PROBE; done; done
for st in hash:ip hash:net hash:ip,port hash:ip,port,ip hash:ip,port,net; do
    if o=$($S create "p-$(echo $st | tr ':,' '--')" $st 2>&1); then echo "ok ipset $st"; else echo "FAIL ipset $st :: $o"; fi
done
for b in $I $I6; do
    t $b filter comment -m comment --comment probe -j ACCEPT
    t $b filter statistic -m statistic --mode random --probability 0.5 -j ACCEPT
    t $b filter recent -m recent --name probe --rcheck --seconds 10 --reap -j ACCEPT
    t $b filter conntrack -m conntrack --ctstate NEW,INVALID -j ACCEPT
    t $b filter addrtype -m addrtype --dst-type LOCAL -j ACCEPT
    t $b filter mark -m mark --mark 0x4000/0x4000 -j ACCEPT
    t $b filter MARK -j MARK --set-xmark 0x4000/0x4000
    t $b filter REJECT -j REJECT
    t $b filter tcp -p tcp -m tcp --dport 80 -j ACCEPT
    t $b filter udp -p udp -m udp --dport 53 -j ACCEPT
    t $b filter multiport -p tcp -m multiport --dports 80,443 -j ACCEPT
    t $b filter limit -m limit --limit 10/minute --limit-burst 10 -j ACCEPT
    t $b filter NFLOG -j NFLOG --nflog-group 100 --nflog-prefix probe
    t $b filter physdev -m physdev --physdev-is-bridged -j ACCEPT
    t $b filter pkttype -m pkttype --pkt-type multicast -j ACCEPT
    t $b nat MASQUERADE -j MASQUERADE --random-fully
    t $b nat comment -m comment --comment probe -j RETURN
done
t $I nat DNAT -p tcp -j DNAT --to-destination 10.0.0.1:80
t $I nat SNAT -j SNAT --to-source 10.0.0.1
t $I6 nat DNAT -p tcp -j DNAT --to-destination [fd00::1]:80
echo "iptables: $($I --version)"
EOF
)
rc=$?
echo "${out}"
[ "${rc}" = 0 ] || { echo "nfprobe: docker run rc=${rc}"; exit 2; }
grep -q '^iptables: .*(nf_tables)' <<< "${out}" || { echo "nfprobe: no nf_tables iptables in ${IMAGE}"; exit 2; }
missing=$(awk '$1 == "FAIL" {print ($2 == "ipset") ? $3 : $4}' <<< "${out}" | sort -u | tr '\n' ' ')
if [ -z "${missing}" ]; then echo "NFPROBE clean"; else echo "NFPROBE missing: ${missing% }"; exit 1; fi
