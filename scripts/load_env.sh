# Sourced by start.sh and scripts/quanta_daily.sh: export KEY=VALUE lines from a
# .env file without executing it. Matches what the Python side accepts
# (core/portfolio/sources.py): values may contain spaces or <angle brackets>
# ("Jane Doe jane@example.com" is SEC's own recommended form), optional quotes
# are stripped, CRLF endings are tolerated. Blank values (as copied from
# .env.example) and variables already set in the environment are left alone.
load_env() {
    local file="$1" line key value
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
        key="${BASH_REMATCH[2]}"
        value="${BASH_REMATCH[3]}"
        value="${value#"${value%%[![:space:]]*}"}"   # trim leading space
        value="${value%"${value##*[![:space:]]}"}"   # trim trailing space
        if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
            value="${BASH_REMATCH[1]}"
        fi
        [[ -z "$value" || -n "${!key:-}" ]] && continue
        export "$key=$value"
    done < "$file"
}
