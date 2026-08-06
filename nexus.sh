#!/usr/bin/env sh
set -eu
export PYTHONUTF8=1

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV_PATH="$PROJECT_ROOT/.venv"
ROOT_MARKER="$VENV_PATH/.nexus-root"
HASH_MARKER="$VENV_PATH/.nexus-pyproject-sha256"
ACTION=${1:-check-registry}
if [ "$#" -gt 0 ]; then
    shift
fi

case "$ACTION" in
    setup|test|check-registry|python) ;;
    *) echo "Unknown action: $ACTION" >&2; exit 2 ;;
esac

if [ -d "$VENV_PATH" ]; then
    RECORDED_ROOT=""
    if [ -f "$ROOT_MARKER" ]; then
        RECORDED_ROOT=$(cat "$ROOT_MARKER")
    fi
    if [ "$RECORDED_ROOT" != "$PROJECT_ROOT" ]; then
        case "$VENV_PATH" in
            "$PROJECT_ROOT/.venv")
                echo "A stale Python environment was found and will be rebuilt."
                rm -rf -- "$VENV_PATH"
                ;;
            *) echo "Refusing to remove an environment outside the project: $VENV_PATH" >&2; exit 2 ;;
        esac
    fi
fi

write_markers() {
    printf '%s\n' "$PROJECT_ROOT" > "$ROOT_MARKER"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$PROJECT_ROOT/pyproject.toml" | awk '{print $1}' > "$HASH_MARKER"
    else
        shasum -a 256 "$PROJECT_ROOT/pyproject.toml" | awk '{print $1}' > "$HASH_MARKER"
    fi
}

cd "$PROJECT_ROOT"
if command -v uv >/dev/null 2>&1; then
    uv sync --locked
    write_markers
    if [ "$ACTION" = "setup" ]; then
        echo "Runtime environment is ready: $VENV_PATH"
        exit 0
    fi
    USE_UV=1
else
    BASE_PYTHON=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            BASE_PYTHON=$candidate
            break
        fi
    done
    if [ -z "$BASE_PYTHON" ]; then
        echo "Python 3.11+ was not found. The future Tavern build will not require Python, but the current source tools require Python or uv." >&2
        exit 2
    fi
    if [ ! -x "$VENV_PATH/bin/python" ]; then
        "$BASE_PYTHON" -m venv "$VENV_PATH"
    fi
    CURRENT_HASH=$(sha256sum "$PROJECT_ROOT/pyproject.toml" 2>/dev/null | awk '{print $1}' || shasum -a 256 "$PROJECT_ROOT/pyproject.toml" | awk '{print $1}')
    INSTALLED_HASH=""
    if [ -f "$HASH_MARKER" ]; then
        INSTALLED_HASH=$(cat "$HASH_MARKER")
    fi
    if [ "$CURRENT_HASH" != "$INSTALLED_HASH" ]; then
        "$VENV_PATH/bin/python" -m pip install --disable-pip-version-check --editable "$PROJECT_ROOT"
    fi
    write_markers
    if [ "$ACTION" = "setup" ]; then
        echo "Runtime environment is ready: $VENV_PATH"
        exit 0
    fi
    USE_UV=0
fi

if [ "$USE_UV" -eq 1 ]; then
    case "$ACTION" in
        test) exec uv run --locked python -m unittest discover -s tests -v "$@" ;;
        check-registry) exec uv run --locked python -m world_simulator_schema.cli check-registry "$@" ;;
        python) exec uv run --locked python "$@" ;;
    esac
else
    case "$ACTION" in
        test) exec "$VENV_PATH/bin/python" -m unittest discover -s tests -v "$@" ;;
        check-registry) exec "$VENV_PATH/bin/python" -m world_simulator_schema.cli check-registry "$@" ;;
        python) exec "$VENV_PATH/bin/python" "$@" ;;
    esac
fi
