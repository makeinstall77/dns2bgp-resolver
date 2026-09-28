#!/bin/sh
# Reload dns2bgp SuffixMatchNode in a running dnsdist.
# Used by dns2bgp after writing aaaa-suppress.domains and by systemd ExecReload.
#
# dnsdist 2.0.x: client -k/--setkey does not authenticate -e/-c sessions
# (always "console key is not valid") and still exits 0. Pass setKey() via a
# temporary client config instead, and treat known failure strings as errors.
set -eu

KEY_FILE="${DNS2BGP_DNSDIST_KEY_FILE:-/etc/dns2bgp/dnsdist.key}"
CONTROL="${DNS2BGP_DNSDIST_CONTROL:-127.0.0.1:5199}"

if [ ! -r "$KEY_FILE" ]; then
    echo "reload-dnsdist: cannot read key file: $KEY_FILE" >&2
    exit 1
fi

KEY="$(tr -d '\n' <"$KEY_FILE")"
if [ -z "$KEY" ]; then
    echo "reload-dnsdist: empty key file: $KEY_FILE" >&2
    exit 1
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
chmod 600 "$TMP"
printf 'setKey("%s")\ncontrolSocket("%s")\n' "$KEY" "$CONTROL" >"$TMP"

# dnsdist often exits 0 even when the console command failed — inspect output.
set +e
OUT="$(dnsdist -C "$TMP" -e "reloadDns2bgpDomains()" 2>&1)"
RC=$?
set -e
if [ -n "$OUT" ]; then
    printf '%s\n' "$OUT"
fi
case "$OUT" in
    *"not valid"*|*"Unable to"*|*"Error while"*|*"Connection refused"*|*"connect: "* )
        exit 1
        ;;
esac
exit "$RC"
