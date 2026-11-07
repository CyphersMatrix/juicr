#!/bin/bash
# Phase 0 isolation battery v2. Run as user juicr inside LXC 641 with a user session.
cd ~
export HOME=/home/juicr XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
IMG=docker.io/library/debian:13
R(){ podman run --rm --network=none --cap-drop=all --security-opt no-new-privileges --read-only --tmpfs /tmp:size=16m "$@" 2>&1 | grep -v 'level='; }
t(){ printf '%-4s %s :: %s\n' "$2" "$1" "$3"; }
echo "== T1 identity / capabilities"
o=$(R $IMG sh -c 'grep -E "CapEff|NoNewPrivs" /proc/self/status; cat /proc/self/uid_map')
echo "$o"
echo "$o" | grep -q 'CapEff:.0000000000000000' && t caps-dropped PASS || t caps-dropped FAIL
echo "$o" | grep -q 'NoNewPrivs:.1' && t no-new-privs PASS || t no-new-privs FAIL
echo "== T2 filesystem"
o=$(R $IMG sh -c 'touch /x 2>&1; ls /home; ls /root; test -e /home/juicr && echo HOSTHOME || echo nohome; test -e /etc/juicr-marker && echo MARK')
echo "$o"
echo "$o" | grep -qi 'read-only' && t rootfs-readonly PASS || t rootfs-readonly FAIL
echo "$o" | grep -q HOSTHOME && t host-home-hidden FAIL || t host-home-hidden PASS
echo "== T3 sockets / mgmt"
o=$(R $IMG sh -c 'find / -xdev \( -name "*.sock" -o -name "podman*" -o -name "docker*" \) 2>/dev/null; ls /run')
echo "$o"
echo "$o" | grep -qE 'podman|docker|\.sock' && t mgmt-socket-absent FAIL || t mgmt-socket-absent PASS
echo "== T4 cross-task contamination"
rm -rf ~/ws; mkdir -p ~/ws
R -v ~/ws:/ws:Z $IMG sh -c 'echo secretA > /ws/a; echo secretA > /tmp/a' >/dev/null
o=$(R $IMG sh -c 'cat /tmp/a 2>&1; ls /ws 2>&1')
echo "$o"
echo "$o" | grep -q secretA && t fresh-task-clean FAIL || t fresh-task-clean PASS
[ -f ~/ws/a ] && t volume-persists-only-if-declared PASS || t volume-persists-only-if-declared FAIL
echo "== T5 network none"
o=$(R $IMG bash -c 'tail -n +3 /proc/net/dev | cut -d: -f1; echo v6=$(grep -vc " lo$" /proc/net/if_inet6); getent hosts deb.debian.org; echo dns=$?; timeout 3 bash -c "exec 3<>/dev/tcp/10.138.6.1/13305" 2>&1; echo lem=$?; timeout 3 bash -c "exec 3<>/dev/tcp/10.138.6.41/22" 2>&1; echo self=$?; timeout 3 bash -c "exec 3<>/dev/tcp/1.1.1.1/53" 2>&1; echo inet=$?')
echo "$o"
echo "$o" | grep -E '^ *(eth|ens|tap|pasta)' >/dev/null && t only-loopback FAIL || t only-loopback PASS
echo "$o" | grep -q 'v6=0' && t no-ipv6-interfaces PASS || t no-ipv6-interfaces FAIL
for k in dns lem self inet; do echo "$o" | grep -q "$k=0" && t "$k-blocked" FAIL || t "$k-blocked" PASS; done
echo "== T6 resource ceilings"
o=$(R --memory=64m --memory-swap=64m $IMG bash -c 'a=; while :; do a+=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX; a+=$a; done; echo survived'); rc=$?
echo "mem: [$o]"
echo "$o" | grep -q survived && t memory-ceiling FAIL || t memory-ceiling PASS "killed"
o=$(R --pids-limit=32 $IMG bash -c 'for i in $(seq 1 200); do sleep 20 & done 2>&1 | head -2; wait 2>/dev/null; ' ); echo "pids: [$o]"
echo "$o" | grep -qiE 'retry|resource|fork' && t pids-ceiling PASS "fork refused" || t pids-ceiling FAIL
o=$(R --tmpfs /w:size=8m $IMG sh -c 'dd if=/dev/zero of=/w/f bs=1M count=64 2>&1; du -sk /w/f'); echo "disk: [$o]"
echo "$o" | grep -qi 'no space' && t disk-ceiling PASS "ENOSPC" || t disk-ceiling FAIL
f(){ s=$(date +%s.%N); R "$@" $IMG sh -c 'i=0; while [ $i -lt 600000 ]; do i=$((i+1)); done' >/dev/null; e=$(date +%s.%N); echo "scale=1; $e-$s" | bc 2>/dev/null || python3 -c "print(round($e-$s,1))"; }
echo "cpu capped(0.25)=$(f --cpus=0.25)s uncapped=$(f)s"
echo "== T7 timeout / orphan cleanup"
podman run -d --rm --network=none --name tmo $IMG sleep 300 >/dev/null 2>&1; sleep 1
podman stop -t 2 tmo >/dev/null 2>&1; sleep 1
[ "$(podman ps -aq | wc -l)" = 0 ] && t stop-cleans-container PASS || t stop-cleans-container FAIL
R --timeout=3 $IMG sleep 60; sleep 2
[ "$(pgrep -u juicr -cx sleep)" = 0 ] && t timeout-kills-task PASS || t timeout-kills-task FAIL "orphan"
podman run -d --network=none --name orph $IMG sleep 300 >/dev/null 2>&1; pkill -9 -x conmon; sleep 1
echo "after conmon kill: containers=$(podman ps -aq | wc -l) sleep=$(pgrep -u juicr -cx sleep)"
podman rm -f -a >/dev/null 2>&1; sleep 1
echo "after rm -f -a: containers=$(podman ps -aq | wc -l) sleep=$(pgrep -u juicr -cx sleep)"
[ "$(podman ps -aq | wc -l)" = 0 ] && [ "$(pgrep -u juicr -cx sleep)" = 0 ] && t orphan-recovery PASS || t orphan-recovery FAIL
