#!/system/bin/sh
HASH=c8750f0d
SRC=/data/local/tmp/$HASH.0
DEST=/system/etc/security/cacerts/$HASH.0

echo "=== 1. remount / rw ==="
mount -o rw,remount / 2>&1
echo "rc=$?"

echo ""
echo "=== 2. install into system trust store ==="
cp -f $SRC $DEST
chown root:root $DEST
chmod 644 $DEST
chcon u:object_r:system_file:s0 $DEST 2>/dev/null
ls -lZ $DEST

echo ""
echo "=== 3. install into user trust store (belt and braces) ==="
mkdir -p /data/misc/user/0/cacerts-added
cp -f $SRC /data/misc/user/0/cacerts-added/$HASH.0
chown system:system /data/misc/user/0/cacerts-added/$HASH.0
chmod 644 /data/misc/user/0/cacerts-added/$HASH.0
mkdir -p /data/misc/keychain/cacerts-added
cp -f $SRC /data/misc/keychain/cacerts-added/$HASH.0
chmod 644 /data/misc/keychain/cacerts-added/$HASH.0
ls -l /data/misc/user/0/cacerts-added/

echo ""
echo "=== 4. persistent magisk module ==="
M=/data/adb/modules/xhs_mitm_ca
mkdir -p $M/system/etc/security/cacerts
cp -f $SRC $M/system/etc/security/cacerts/$HASH.0
chmod 644 $M/system/etc/security/cacerts/$HASH.0
cat > $M/module.prop <<'EOF'
id=xhs_mitm_ca
name=MITM CA Injector
version=1.0
versionCode=1
author=WKnx
description=Injects mitmproxy root CA into system trust store for authorized traffic analysis
EOF
echo "updateDescription=Injects mitmproxy CA (c8750f0d) into system trust store" > $M/update
touch $M/disable 2>/dev/null; rm -f $M/disable
ls -lR $M

echo ""
echo "=== 5. verify count ==="
ls /system/etc/security/cacerts/ | wc -l

echo ""
echo "=== 6. verify mitmproxy CA openssl-readable ==="
ls -l $DEST
head -1 $DEST
