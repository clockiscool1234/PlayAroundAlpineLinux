#!/bin/sh
set -eu

keys_path=${KEYS_PATH:-/keys}
mkdir -p "$keys_path"

for type in rsa ed25519; do
    key="$keys_path/ssh_host_${type}_key"
    if [ ! -f "$key" ]; then
        ssh-keygen -q -t "$type" -N "" -f "$key"
    fi
done

chmod 600 "$keys_path"/ssh_host_rsa_key "$keys_path"/ssh_host_ed25519_key
chmod 644 "$keys_path"/ssh_host_rsa_key.pub "$keys_path"/ssh_host_ed25519_key.pub
