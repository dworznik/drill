# Drill one skill: `just drill <skill>`, where <skill> names a rig under skills/.
drill $skill:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! "$skill" =~ ^[[:alnum:]_-]+$ ]]; then
        echo "drill: invalid skill name: $skill" >&2
        exit 1
    fi
    rig="skills/$skill"
    if [[ ! -d "$rig" ]]; then
        echo "drill: no rig at $rig" >&2
        exit 1
    fi
    echo "drill: $skill: nothing to run yet"
