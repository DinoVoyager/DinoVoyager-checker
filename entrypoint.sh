#!/bin/sh
set -e

# If arguments start with a dash flag or no arguments are provided,
# execute the checker module with all forwarded arguments.
if [ $# -eq 0 ] || [ "${1#-}" != "$1" ]; then
    exec python3 -m checker "$@"
fi

# Otherwise, allow executing arbitrary container commands (e.g. sh, env)
exec "$@"
