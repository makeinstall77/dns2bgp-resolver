#!/bin/sh
# Reload dns2bgp SuffixMatchNode in a running dnsdist.
# Used by dns2bgp after writing aaaa-suppress.domains and by systemd ExecReload.
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

exec dnsdist -C /dev/null -k "$KEY" -e "reloadDns2bgpDomains()" -c "$CONTROL"
