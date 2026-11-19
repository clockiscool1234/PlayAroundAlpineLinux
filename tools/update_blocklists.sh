#!/bin/sh
set -eu

git submodule update --init --depth 1
git submodule update --remote --depth 1 blocklists/bad-websites blocklists/Phishing.Database

echo "Blocklist submodules updated. Review and commit the new submodule revisions."
echo "Restart the filter with: docker compose restart network-filter"
