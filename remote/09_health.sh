#!/bin/bash
S=$(docker inspect --format '{{.State.Status}}' windows 2>/dev/null || echo none)
R=$(docker inspect --format '{{.RestartCount}}' windows 2>/dev/null || echo 0)
W=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8006 || true)
[ -z "$W" ] && W=000
D=$(ss -ltn 2>/dev/null | grep -c ':3389' || true)
F=$(df -h /mnt/data 2>/dev/null | tail -1 | tr -s ' ')
echo "STATUS=$S"
echo "RESTARTS=$R"
echo "WEB=$W"
echo "RDP=$D"
echo "DISK=$F"
echo "HEALTH_DONE"
