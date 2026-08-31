#!/bin/bash
# Tager JSON-output fra en AI (se MADBANK_RULES.md) og tilføjer nye retter til madbank.json.
# Dubletter (samme navn som findes i forvejen) frasorteres automatisk.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Henter seneste version..."
git pull origin main --quiet --no-edit

echo
echo "Indsæt AI'ens JSON-svar herunder, afslut med Ctrl-D på en tom linje:"
echo

NEW_JSON=$(cat)

RESULT=$(python3 - "$NEW_JSON" << 'PYEOF'
import json, sys

new_json_raw = sys.argv[1]

# Nogle AI'er pakker svaret ind i en ```json ... ``` kodeblok trods instruktion om ikke at gøre det.
stripped = new_json_raw.strip()
if stripped.startswith("```"):
    lines = stripped.splitlines()
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    stripped = "\n".join(lines)

try:
    new_dishes = json.loads(stripped)
except json.JSONDecodeError as e:
    print(f"FEJL: kunne ikke læse JSON ({e})", file=sys.stderr)
    sys.exit(1)

if not isinstance(new_dishes, list):
    print("FEJL: forventede et JSON-array af retter.", file=sys.stderr)
    sys.exit(1)

with open("madbank.json") as f:
    data = json.load(f)

existing_names_lower = {m["name"].strip().lower() for m in data["meals"]}

to_add = []
skipped = []
for dish in new_dishes:
    name = dish.get("name", "").strip()
    if not name:
        continue
    if name.lower() in existing_names_lower:
        skipped.append(name)
        continue
    to_add.append({
        "name": name,
        "recipeURL": dish.get("recipeURL", ""),
        "items": dish.get("items", []),
    })
    existing_names_lower.add(name.lower())

if skipped:
    print("SKIPPED:" + "|".join(skipped))

if not to_add:
    print("NOTHING_TO_ADD")
    sys.exit(0)

data["meals"].extend(to_add)

with open("madbank.json", "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")

print("ADDED:" + "|".join(d["name"] for d in to_add))
PYEOF
)

SKIPPED_LINE=$(echo "$RESULT" | grep "^SKIPPED:" || true)
ADDED_LINE=$(echo "$RESULT" | grep "^ADDED:" || true)

if [ -n "$SKIPPED_LINE" ]; then
    echo
    echo "Sprunget over (findes allerede):"
    echo "${SKIPPED_LINE#SKIPPED:}" | tr '|' '\n' | sed 's/^/  - /'
fi

if echo "$RESULT" | grep -q "^NOTHING_TO_ADD$"; then
    echo
    echo "Intet nyt at tilføje — alle retter fandtes allerede."
    exit 0
fi

echo
echo "Tilføjes:"
echo "${ADDED_LINE#ADDED:}" | tr '|' '\n' | sed 's/^/  + /'

echo
read -rp "Commit og push? (j/n): " CONFIRM
case "$CONFIRM" in
    [jJ]*)
        git add madbank.json
        git commit -q -m "Tilføj retter fra AI"
        git push origin main --quiet
        echo "Færdig! Retterne er live i appen med det samme."
        ;;
    *)
        git checkout -- madbank.json
        echo "Fortrudt — ingen ændringer gemt."
        ;;
esac
