D=$HOME/k3dgpu-bench/runs/canary-321
mkdir -p "$D"
rm -f "$D/lock-taken" "$D/holder.pid"
setsid nohup flock -n -E 75 "$HOME/k3dgpu-bench.lock" sh -c "echo \$\$ > $D/holder.pid; date -u +%FT%TZ > $D/lock-taken; exec sleep 1200" > "$D/holder.log" 2>&1 < /dev/null &
sleep 3
if [ -s "$D/lock-taken" ]; then echo "taken $(cat "$D/lock-taken") holder $(cat "$D/holder.pid")"; exit 0; fi
exit 1
