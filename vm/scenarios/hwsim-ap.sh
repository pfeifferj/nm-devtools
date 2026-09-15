#!/bin/sh
# Run via `nm-vm scenario` in a snapshotted guest and roll back afterwards.
set -eu

if [ -d /sys/module/mac80211_hwsim ]; then
  echo "mac80211_hwsim is already loaded; restore the pre-scenario snapshot first" >&2
  exit 1
fi
for iface in wlan0 wlan1 wlan2; do
  if [ -e "/sys/class/net/$iface" ]; then
    echo "$iface already exists; this scenario needs three unused wlan names" >&2
    exit 1
  fi
done
nmcli general status >/dev/null

if ! { command -v hostapd && command -v dnsmasq && command -v wpa_supplicant; } >/dev/null; then
  dnf install -y --refresh hostapd dnsmasq wpa_supplicant
fi
for command in hostapd dnsmasq wpa_supplicant; do
  command -v "$command" >/dev/null
done

# mac80211_hwsim is in kernel-modules-internal and must match the running
# kernel. Older Fedora kernels may only be available from Koji.
if ! modinfo mac80211_hwsim >/dev/null 2>&1; then
  # shellcheck source=/dev/null
  . /etc/os-release
  if [ "$ID" != fedora ]; then
    echo "install mac80211_hwsim for the running kernel before using this scenario" >&2
    exit 1
  fi
  dnf install -y --refresh "kernel-modules-internal-$(uname -r)" || {
    r=$(uname -r); a=${r##*.}; vr=${r%.*}
    koji="https://kojipkgs.fedoraproject.org/packages/kernel/${vr%%-*}/${vr#*-}/$a"
    dnf install -y "$koji/kernel-modules-$vr.$a.rpm" \
      "$koji/kernel-modules-internal-$vr.$a.rpm"
  }
fi

conf=$(mktemp -d /run/nm-hwsim-ap.XXXXXX)
loaded=no
cleanup() {
  status=$?
  trap - 0 HUP INT TERM
  if [ "$status" -ne 0 ]; then
    for pidfile in "$conf"/*.pid; do
      [ ! -f "$pidfile" ] || kill "$(cat "$pidfile")" 2>/dev/null || :
    done
    if [ "$loaded" = yes ]; then
      modprobe -r mac80211_hwsim || :
    fi
    rm -rf "$conf"
  fi
  exit "$status"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

modprobe mac80211_hwsim radios=3
loaded=yes
attempt=0
until nmcli -g GENERAL.DEVICE device show wlan2 >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "NetworkManager did not discover the hwsim radios" >&2
    exit 1
  fi
  sleep 0.5
done

nmcli device set wlan0 managed yes
nmcli device set wlan1 managed no
nmcli device set wlan2 managed no
nmcli radio wifi on

cat > "$conf/open.conf" <<'EOF'
interface=wlan1
driver=nl80211
ssid=hwsim-open
hw_mode=g
channel=1
EOF
cat > "$conf/wpa2.conf" <<'EOF'
interface=wlan2
driver=nl80211
ssid=hwsim-wpa2
hw_mode=g
channel=6
wpa=2
wpa_passphrase=hwsim-secret
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

hostapd -B -P "$conf/open.pid" "$conf/open.conf"
hostapd -B -P "$conf/wpa2.pid" "$conf/wpa2.conf"

if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --add-interface=wlan1 --zone=trusted
  firewall-cmd --add-interface=wlan2 --zone=trusted
fi

ip addr add dev wlan1 172.25.14.1/24
ip addr add dev wlan2 172.25.15.1/24
dnsmasq --conf-file=/dev/null --port=0 --except-interface=lo \
  --interface=wlan1 --interface=wlan2 --bind-interfaces \
  --pid-file="$conf/dnsmasq.pid" --dhcp-leasefile="$conf/leases" \
  --dhcp-option=option:router --dhcp-option=option:dns-server \
  --dhcp-range=172.25.14.100,172.25.14.200 \
  --dhcp-range=172.25.15.100,172.25.15.200

nmcli device wifi rescan ifname wlan0
echo "APs up: hwsim-open (wlan1, 172.25.14.0/24), hwsim-wpa2 (wlan2, 172.25.15.0/24, psk hwsim-secret)"
