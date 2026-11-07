#!/bin/bash
# Push the working tree to LXC 641 (/home/juicr/app) and run the tests there.
set -e
cd "$(dirname "$0")/.."
T=$(mktemp)
tar --exclude=.git --exclude=__pycache__ --exclude=.venv -czf "$T" .
scp -q -i ~/.ssh/proxispace_root "$T" root@10.138.6.1:/tmp/juicr.tgz; rm "$T"
ssh -i ~/.ssh/proxispace_root root@10.138.6.1 'pct push 641 /tmp/juicr.tgz /home/juicr/juicr.tgz --user 1000 && rm /tmp/juicr.tgz && pct exec 641 -- runuser -u juicr -- bash -c "cd ~ && tar xzf juicr.tgz -C app && rm juicr.tgz && cd app && ~/venv/bin/python -m unittest discover -s tests ${JUICR_TEST_ARGS:-}"'
