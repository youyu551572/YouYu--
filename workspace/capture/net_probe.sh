#!/system/bin/sh
echo "=== interfaces ==="
ip addr show 2>/dev/null | grep -E 'inet |^[0-9]+:' 
echo ""
echo "=== routes ==="
ip route 2>/dev/null
echo ""
echo "=== default gw ==="
ip route | grep default
echo ""
echo "=== busybox ifconfig ==="
ifconfig 2>/dev/null | grep -E 'inet|UP'
echo ""
echo "=== dns ==="
getprop | grep -i dns
