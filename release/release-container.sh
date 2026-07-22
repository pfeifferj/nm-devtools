#!/bin/bash

# Run the NetworkManager release process (release.sh) inside a Fedora
# container, so a release can be cut from any host that has podman (not just a
# Fedora box).
#
# Clone and push go over HTTPS using the api token (oauth2:<token>@), so no
# ssh-agent or ssh key is needed. Tag signing uses a smartcard (Nitrokey): the
# USB device is passed into the container, which runs its own scdaemon/gpg-agent
# and prompts for the card PIN with pinentry-curses on its own terminal. No gpg
# private key is copied into the container.
#
# Usage:
#   NM_RELEASE_TOKEN=<gitlab-api-token> \
#     release/release-container.sh devel|rc1|rc|major|major-post|minor [release.sh args...]
# (also symlinked at contrib/fedora/rpm/release-container.sh in the NM checkout)
#
# Must run from an interactive terminal: the card has a forced signature PIN, so
# pinentry runs once per signed tag (rc1 signs two).
#
# By default this is a dry run (release.sh does not push); pass --no-test for
# the real thing. The dry run still builds the RPMs and tarball inside the
# container, which validates the build on Fedora before any push. Built tarballs
# are copied to $NM_RELEASE_OUT (default: ./release-out).
#
# Env overrides:
#   NM_RELEASE_TOKEN    GitLab api token, needs api scope (required)
#   NM_RELEASE_HOST     GitLab host (default: gitlab.freedesktop.org)
#   NM_RELEASE_PROJECT  project path (default: NetworkManager/NetworkManager)
#   NM_RELEASE_SIGNKEY  gpg key id for signing (default: git config user.signingkey)
#   NM_RELEASE_CARD_USB smartcard USB vendor:product (default: 20a0:42b2, Nitrokey 3A)
#   NM_RELEASE_BRANCH   branch to release from (default: the clone's default
#                       branch, i.e. main; set nm-1-X for rc/major/minor)
#   NM_RELEASE_OUT      host dir for built tarballs (default: ./release-out)
#   NM_SRC              NetworkManager checkout (default: ~/rh-src/NetworkManager)

set -e

die() { echo "release-container: $*" >&2; exit 1; }

DIR="$(realpath "${NM_SRC:-$HOME/rh-src/NetworkManager}")"
[ -f "$DIR/contrib/fedora/rpm/release.sh" ] || die "$DIR is not a NetworkManager checkout (set NM_SRC)"
cd "$DIR"

[ "$#" -ge 1 ] || die "give a release mode (devel|rc1|rc|major|major-post|minor)"
[ -t 0 ] || die "must run from an interactive terminal (the card needs a PIN via pinentry)"

FEDORA_VERSION="$(sed '/^    tier: 1/,/^  - name/!d' .gitlab-ci/config.yml | sed -n "s/^      - '\([0-9]\+\)'$/\1/p" | sed -n 1p)"
[ -n "$FEDORA_VERSION" ] || die "cannot detect tier-1 Fedora version from .gitlab-ci/config.yml"
IMAGE="nm-release:f$FEDORA_VERSION"

: "${NM_RELEASE_HOST:=gitlab.freedesktop.org}"
: "${NM_RELEASE_PROJECT:=NetworkManager/NetworkManager}"
: "${NM_RELEASE_SIGNKEY:=$(git config --get user.signingkey)}"
: "${NM_RELEASE_CARD_USB:=20a0:42b2}"
: "${NM_RELEASE_OUT:=$DIR/release-out}"
[ -n "$NM_RELEASE_TOKEN" ]   || die "set NM_RELEASE_TOKEN to a GitLab api token"
[ -n "$NM_RELEASE_SIGNKEY" ] || die "set NM_RELEASE_SIGNKEY or git config user.signingkey"

if ! podman image exists "$IMAGE"; then
    echo "Building $IMAGE ..."
    podman build --tag "$IMAGE" -f - "$DIR" <<EOF
FROM fedora:$FEDORA_VERSION
COPY contrib/fedora/REQUIRED_PACKAGES /root/REQUIRED_PACKAGES
RUN dnf -y upgrade \
 && NM_NO_EXTRA=1 NM_INSTALL="dnf install -y --allowerasing" bash /root/REQUIRED_PACKAGES \
 && dnf install -y gnupg2 pinentry pcsc-lite pcsc-lite-ccid libusb1 \
 && dnf clean all
EOF
fi

# Locate the smartcard's USB node. Bus/device numbers renumber on replug, so
# derive them from the vendor:product id rather than hardcoding.
read -r BUS DEV < <(lsusb -d "$NM_RELEASE_CARD_USB" | sed -nE 's/^Bus ([0-9]+) Device ([0-9]+):.*/\1 \2/p' | head -1)
[ -n "$BUS" ] || die "smartcard $NM_RELEASE_CARD_USB not found on USB (is it plugged in?)"
USB_NODE="/dev/bus/usb/$BUS/$DEV"
[ -e "$USB_NODE" ] || die "USB node $USB_NODE does not exist"

CLONE_URL="https://oauth2:${NM_RELEASE_TOKEN}@${NM_RELEASE_HOST}/${NM_RELEASE_PROJECT}.git"

# Check the token before the ~25 min build; release.sh only checks it after.
# The endpoint intermittently returns gateway errors, retry with backoff.
BACKOFF=5
for i in 1 2 3 4 5; do
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
        --header "PRIVATE-TOKEN: $NM_RELEASE_TOKEN" \
        "https://$NM_RELEASE_HOST/api/v4/personal_access_tokens/self")" || CODE=000
    [ "$CODE" = 200 ] && break
    echo "release-container: token preflight got HTTP $CODE (attempt $i/5)" >&2
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
done
[ "$CODE" = 200 ] || die "token preflight failed (HTTP $CODE): check NM_RELEASE_TOKEN (api scope) and https://$NM_RELEASE_HOST"

FPR="$(gpg --fingerprint --with-colons "$NM_RELEASE_SIGNKEY" | awk -F: '/^fpr:/{print $10; exit}')"
[ -n "$FPR" ] || die "cannot find gpg key $NM_RELEASE_SIGNKEY"
PUBKEY="$(mktemp)"
trap 'rm -f "$PUBKEY"' EXIT
gpg --export "$NM_RELEASE_SIGNKEY" > "$PUBKEY"
mkdir -p "$NM_RELEASE_OUT"
LOG="$NM_RELEASE_OUT/release-$1.log"

# Release the card from the host so the container's scdaemon can claim it. The
# host gpg-agent respawns scdaemon on demand later. Do not use gpg on the host
# during the run, or the host re-grabs the card and the container loses it.
gpgconf --kill scdaemon 2>/dev/null || true

# -it: the container needs a real terminal so its pinentry-curses can prompt for
# the card PIN. Output is NOT piped on the host (a pipe breaks -t and curses);
# logging and token redaction happen inside the container instead.
#
# Bind-mount the whole /dev/bus/usb so libusb/CCID can enumerate the reader (a
# single --device node is not enough to find it). Rootless podman maps container
# root to the host uid, which the card node's ACL grants access to. keep-groups
# preserves host supplementary groups in case the reader is group-owned.
# shellcheck disable=SC2016
podman run --rm -it \
    --network host \
    -v /dev/bus/usb:/dev/bus/usb \
    -v /run/udev:/run/udev:ro \
    --group-add keep-groups \
    -e NM_RELEASE_TOKEN \
    -e CLONE_URL="$CLONE_URL" \
    -e SIGNKEY="$NM_RELEASE_SIGNKEY" \
    -e FPR="$FPR" \
    -e MODE="$1" \
    -e BRANCH="$NM_RELEASE_BRANCH" \
    -e GIT_NAME="$(git config --get user.name)" \
    -e GIT_EMAIL="$(git config --get user.email)" \
    -v "$PUBKEY:/root/pubkey.gpg:ro" \
    -v "$NM_RELEASE_OUT:/out:Z" \
    "$IMAGE" \
    bash -euc '
        install -d -m 700 ~/.gnupg
        printf "pinentry-program /usr/bin/pinentry-curses\npinentry-timeout 300\n" > ~/.gnupg/gpg-agent.conf
        gpg --import /root/pubkey.gpg
        echo "$FPR:6:" | gpg --import-ownertrust

        # Fedora scdaemon reaches the card through pcscd. Start it with polkit
        # auth disabled: there is no D-Bus/polkit in the container, and pcscd
        # would otherwise reject scdaemon as an unauthorized client.
        pcscd --disable-polkit 2>/dev/null || true
        gpg --card-status   # generates the card key stubs; fails loudly if no reader

        # rc1 signs two tags with a ~10 min build between them. The card powers
        # down / USB-autosuspends when idle, so the second signature can hit
        # "please insert the card". A light periodic probe keeps it awake.
        ( while :; do gpg-connect-agent "SCD SERIALNO" /bye >/dev/null 2>&1 || true; sleep 60; done ) &
        trap "kill $! 2>/dev/null || true" EXIT

        git clone "$CLONE_URL" /src
        cd /src
        [ -z "$BRANCH" ] || git checkout "$BRANCH"
        git fetch -q origin "refs/notes/*:refs/notes/*"   # find-backports needs refs/notes/bugs
        git config user.name  "${GIT_NAME:-NetworkManager}"
        git config user.email "${GIT_EMAIL:-networkmanager@example.com}"
        git config user.signingkey "$SIGNKEY"
        git config commit.gpgsign true

        # release.sh output is piped to tee below, so gpg cannot autodetect the
        # terminal; point GPG_TTY at the container pty (from -it) so pinentry-curses
        # can draw the PIN prompt. Without this it fails with "Inappropriate ioctl".
        GPG_TTY="$(tty)"; export GPG_TTY
        gpg-connect-agent updatestartuptty /bye >/dev/null 2>&1 || true

        set -o pipefail
        ./contrib/fedora/rpm/release.sh "$@" --gitlab-token "$NM_RELEASE_TOKEN" 2>&1 \
            | stdbuf -oL sed -e "s|${NM_RELEASE_TOKEN}|<REDACTED>|g" | tee "/out/release-$MODE.log"

        cp -v /tmp/NetworkManager-*.tar.xz /tmp/NetworkManager-*.sha256sum /out/ 2>/dev/null || true
    ' _ "$@"
echo "release-container: full log at $LOG" >&2
