# Drill one skill: `just drill <path-to-rig>`, where the rig is a directory
# in the rigs repo holding `rig.json` and `cases/`.
drill $rig:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -d "$rig" ]]; then
        echo "drill: no rig directory at $rig" >&2
        exit 1
    fi
    if [[ ! -f "$rig/rig.json" ]]; then
        echo "drill: $rig has no rig.json" >&2
        exit 1
    fi
    if [[ ! -d "$rig/cases" ]]; then
        echo "drill: $rig has no cases/ directory" >&2
        exit 1
    fi
    echo "drill: $rig: nothing to run yet"
