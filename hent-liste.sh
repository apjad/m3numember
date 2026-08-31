#!/bin/bash
# Viser den nuværende madbank-liste — kopiér output med ind i AI-promptet for at undgå dubletter.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git pull origin main --quiet --no-edit

python3 -c "
import json
with open('madbank.json') as f:
    data = json.load(f)
names = [m['name'] for m in data['meals']]
print(f'Nuværende liste ({len(names)} retter):')
print(', '.join(names))
"
