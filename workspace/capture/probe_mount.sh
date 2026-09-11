#!/system/bin/sh
echo "=== /proc/mounts (root/system) ==="
cat /proc/mounts | grep -E ' / | /system ' 
echo ""
echo "=== block devices ==="
ls -l /dev/block/by-name/ 2>/dev/null | head -30
echo ""
echo "=== overlayfs modules ==="
ls /data/adb/modules/
echo ""
echo "=== try remount / rw ==="
mount -o rw,remount / 2>&1
echo "rc=$?"
touch /system/etc/security/cacerts/.wtest 2>&1 && echo "WRITABLE" && rm -f /system/etc/security/cacerts/.wtest || echo "STILL_READONLY"
echo ""
echo "=== magisk overlay support ==="
magisk --path 2>/dev/null
ls -l /sbin/.magisk 2>/dev/null || ls -l /debug_ramdisk/.magisk 2>/dev/null
