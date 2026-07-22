#!/bin/bash

# Run the manual tier2 jobs of a release pipeline so release.sh's
# check_gitlab_pipeline passes: play every manual "tier2:*" job, wait for
# the image builds, then retry the skipped "t_*" tier2 test jobs and wait for
# them to finish.
#
# Usage: NM_RELEASE_TOKEN=<api-token> ./run-tier2.sh <pipeline-id>

set -euo pipefail

PIPELINE="${1:?usage: run-tier2.sh <pipeline-id>}"
API="https://gitlab.freedesktop.org/api/v4/projects/NetworkManager%2FNetworkManager"
AUTH=(-H "PRIVATE-TOKEN: ${NM_RELEASE_TOKEN:?set NM_RELEASE_TOKEN}")

jobs_json() { curl -sf "${AUTH[@]}" "$API/pipelines/$PIPELINE/jobs?per_page=100"; }

ids() { # ids <status> <name-prefix>
    jobs_json | python3 -c "import json,sys
print(' '.join(str(j['id']) for j in json.load(sys.stdin)
               if j['status']=='$1' and j['name'].startswith('$2')))"
}

wait_stage() {
    while :; do
        j="$(jobs_json)" || j=""
        if [ -z "$j" ]; then
            echo "  api hiccup, retrying..."
            sleep 30
            continue
        fi
        left="$(python3 -c "import json,sys
print(sum(1 for j in json.load(sys.stdin)
          if j['stage']=='tier2' and j['status'] in
             ('running','pending','created','waiting_for_resource','preparing','scheduled')))" <<<"$j")"
        [ "$left" = 0 ] && break
        echo "  $left tier2 jobs still running..."
        sleep 60
    done
}

prep_ids="$(ids manual tier2:)"
for id in $prep_ids; do
    echo "play prep job $id"
    curl -sf -X POST "${AUTH[@]}" "$API/jobs/$id/play" >/dev/null
done
wait_stage

test_ids="$(ids skipped t_)"
for id in $test_ids; do
    echo "retry test job $id"
    curl -sf -X POST "${AUTH[@]}" "$API/jobs/$id/retry" >/dev/null
done
wait_stage

jobs_json | python3 -c "import json,sys
bad=[(j['name'],j['status']) for j in json.load(sys.stdin)
     if j['stage']=='tier2' and j['status']!='success']
print('all tier2 jobs green' if not bad else 'NOT green: %s'%bad)
sys.exit(1 if bad else 0)"
