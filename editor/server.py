#!/usr/bin/env python3
"""Local web editor for madbank.json — no external dependencies, stdlib only.

Serves a small UI to add/edit/delete dishes, plus a "Gem og synkroniser"
action that commits and pushes madbank.json to GitHub (same repo the
tilfoej-ret.sh / hent-liste.sh scripts already work against).

Every mutation is scoped to a single dish (POST to add, PUT/DELETE by name)
and is applied against whatever is on disk *at request time* — never a
whole-list overwrite from a possibly-stale browser snapshot. That's the fix
for a real data-loss incident: two people editing at once, and the one who
saved last silently wiped out the other's new dishes.

Protected with HTTP Basic Auth so it's safe to port-forward — credentials
come from madbank-editor-credentials.local (sibling of this repo, one level
up from agentclaude/m3numember-pages).
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MADBANK_PATH = os.path.join(REPO_DIR, "madbank.json")
CREDS_PATH = os.path.expanduser("~/agentclaude/madbank-editor-credentials.local")
GROK_BIN = os.path.expanduser("~/.grok/bin/grok")
PORT = 8420

# Guards every read-modify-write of madbank.json — the whole point of the
# scoped-mutation design is defeated if two concurrent requests can still
# interleave a read and a write.
FILE_LOCK = threading.Lock()


def load_credentials():
    """One 'username:password' per line; '#'-prefixed and blank lines are ignored."""
    users = {}
    try:
        with open(CREDS_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                username, sep, password = line.partition(":")
                if sep and username and password:
                    users[username] = password
    except FileNotFoundError:
        pass
    if not users:
        sys.exit(f"Mangler login i {CREDS_PATH} — kan ikke starte serveren uden.")
    return users


AUTH_USERS = load_credentials()
INDEX_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


def clean_meal(meal, index_label):
    if not isinstance(meal, dict):
        raise ValueError(f"{index_label} er ikke et gyldigt objekt")
    name = str(meal.get("name", "")).strip()
    if not name:
        raise ValueError(f"{index_label} mangler et navn")
    recipe_url = str(meal.get("recipeURL", "")).strip()
    recipe = str(meal.get("recipe", "")).strip()
    items = [str(item).strip() for item in meal.get("items", []) if str(item).strip()]
    return {"name": name, "recipeURL": recipe_url, "recipe": recipe, "items": items}


def load_meals_unlocked():
    with open(MADBANK_PATH, encoding="utf-8") as f:
        return json.load(f)["meals"]


def save_meals_unlocked(meals):
    with open(MADBANK_PATH, "w", encoding="utf-8") as f:
        json.dump({"meals": meals}, f, indent=2, ensure_ascii=False)
        f.write("\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "MadbankEditor/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _check_auth(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
        except Exception:
            return False
        return AUTH_USERS.get(user) == password

    def _require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Madbank"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _meal_name_from_path(self, prefix):
        """Decodes the '/api/meals/<name>' suffix, or None if the path doesn't match."""
        if not self.path.startswith(prefix):
            return None
        return urllib.parse.unquote(self.path[len(prefix):])

    def do_GET(self):
        if not self._check_auth():
            return self._require_auth()
        if self.path == "/":
            with open(INDEX_HTML_PATH, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/meals":
            with FILE_LOCK:
                meals = load_meals_unlocked()
            self._send_json(200, {"meals": meals})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._check_auth():
            return self._require_auth()
        if self.path == "/api/meals":
            return self._add_meal()
        if self.path == "/api/sync":
            return self._send_json(200, self._run_sync())
        if self.path == "/api/suggest-items":
            try:
                name = self._read_json_body().get("name", "").strip()
            except json.JSONDecodeError:
                name = ""
            if not name:
                return self._send_json(400, {"error": "Mangler navn på retten"})
            return self._send_json(200, self._suggest_items(name))
        if self.path == "/api/suggest-items-from-url":
            try:
                url = self._read_json_body().get("url", "").strip()
            except json.JSONDecodeError:
                url = ""
            if not url:
                return self._send_json(400, {"error": "Mangler opskriftslink"})
            return self._send_json(200, self._suggest_items_from_url(url))
        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        if not self._check_auth():
            return self._require_auth()
        original_name = self._meal_name_from_path("/api/meals/")
        if original_name is None:
            self.send_response(404)
            self.end_headers()
            return
        self._edit_meal(original_name)

    def do_DELETE(self):
        if not self._check_auth():
            return self._require_auth()
        original_name = self._meal_name_from_path("/api/meals/")
        if original_name is None:
            self.send_response(404)
            self.end_headers()
            return
        self._delete_meal(original_name)

    def _add_meal(self):
        try:
            new_meal = clean_meal(self._read_json_body(), "Retten")
        except (json.JSONDecodeError, ValueError) as e:
            return self._send_json(400, {"error": str(e)})
        with FILE_LOCK:
            meals = load_meals_unlocked()
            if any(m["name"].lower() == new_meal["name"].lower() for m in meals):
                return self._send_json(409, {"error": f'"{new_meal["name"]}" findes allerede'})
            meals.append(new_meal)
            save_meals_unlocked(meals)
        self._send_json(200, {"ok": True, "meals": meals, "sync": self._run_sync()})

    def _edit_meal(self, original_name):
        try:
            updated = clean_meal(self._read_json_body(), "Retten")
        except (json.JSONDecodeError, ValueError) as e:
            return self._send_json(400, {"error": str(e)})
        with FILE_LOCK:
            meals = load_meals_unlocked()
            index = next((i for i, m in enumerate(meals) if m["name"].lower() == original_name.lower()), None)
            if index is None:
                return self._send_json(404, {
                    "error": f'"{original_name}" findes ikke længere — nogen har nok allerede ændret den. Genindlæs listen.'
                })
            renamed = updated["name"].lower() != original_name.lower()
            if renamed and any(i != index and m["name"].lower() == updated["name"].lower() for i, m in enumerate(meals)):
                return self._send_json(409, {"error": f'"{updated["name"]}" findes allerede'})
            meals[index] = updated
            save_meals_unlocked(meals)
        self._send_json(200, {"ok": True, "meals": meals, "sync": self._run_sync()})

    def _delete_meal(self, original_name):
        with FILE_LOCK:
            meals = load_meals_unlocked()
            filtered = [m for m in meals if m["name"].lower() != original_name.lower()]
            if len(filtered) != len(meals):
                save_meals_unlocked(filtered)
            meals = filtered
        # Already gone (someone else deleted it too) is a benign race, not an error.
        self._send_json(200, {"ok": True, "meals": meals, "sync": self._run_sync()})

    def _suggest_items(self, name):
        prompt = (
            f"List the typical grocery/shopping-list ingredients for the Danish home-cooking "
            f'dish "{name}". Respond with 4-8 short Danish ingredient names, shopping-list '
            f"style (not steps or instructions)."
        )
        schema = json.dumps({
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
        })
        try:
            result = subprocess.run(
                [GROK_BIN, "-p", prompt, "--json-schema", schema, "--disable-web-search"],
                capture_output=True, text=True, timeout=90,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"ok": False, "error": str(e)}
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "grok fejlede"}
        try:
            items = json.loads(result.stdout)["structuredOutput"]["items"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"ok": False, "error": "Kunne ikke aflæse svar fra AI"}
        return {"ok": True, "items": [str(i).strip() for i in items if str(i).strip()]}

    def _suggest_items_from_url(self, url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception as e:
            return {"ok": False, "error": f"Kunne ikke hente siden: {e}"}

        # Strip script/style blocks and collapse whitespace — cuts noise and token cost, the
        # model can still make sense of the remaining tags/text to find the ingredient list.
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        text = text[:20000]

        prompt = (
            "Extract from this Danish recipe webpage (raw HTML/text below): "
            "1) the ingredient list, each as a short shopping-list-style item name, in Danish, "
            "without quantities or units. "
            "2) the fremgangsmåde (method/steps), as short numbered steps in Danish — the core "
            "action of each step, not restated ingredient quantities. Empty string if the page "
            "has no real recipe steps. "
            "Ignore navigation, ads, comments and unrelated content.\n\n" + text
        )
        schema = json.dumps({
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
                "recipe": {"type": "string"},
            },
            "required": ["items", "recipe"],
        })
        try:
            result = subprocess.run(
                [GROK_BIN, "-p", prompt, "--json-schema", schema, "--disable-web-search"],
                capture_output=True, text=True, timeout=90,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"ok": False, "error": str(e)}
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or "grok fejlede"}
        try:
            structured = json.loads(result.stdout)["structuredOutput"]
            items = structured["items"]
            recipe = str(structured.get("recipe", "")).strip()
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"ok": False, "error": "Kunne ikke aflæse svar fra AI"}
        if not items:
            return {"ok": False, "error": "Fandt ingen ingredienser på siden"}
        return {"ok": True, "items": [str(i).strip() for i in items if str(i).strip()], "recipe": recipe}

    def _run_sync(self):
        def run(*args):
            return subprocess.run(
                args, cwd=REPO_DIR, capture_output=True, text=True, timeout=30
            )

        with FILE_LOCK:
            pull = run("git", "pull", "origin", "main", "--quiet", "--no-edit")
            if pull.returncode != 0:
                return {"ok": False, "step": "pull", "log": pull.stderr}

            status = run("git", "status", "--porcelain", "madbank.json")
            if not status.stdout.strip():
                return {"ok": True, "changed": False, "log": "Ingen ændringer at synkronisere."}

            run("git", "add", "madbank.json")
            commit = run("git", "commit", "-q", "-m", "Opdater madbank via web-editor")
            if commit.returncode != 0:
                return {"ok": False, "step": "commit", "log": commit.stderr}

            push = run("git", "push", "origin", "main", "--quiet")
            if push.returncode != 0:
                return {"ok": False, "step": "push", "log": push.stderr}

        return {"ok": True, "changed": True, "log": "Sendt til GitHub — live om et øjeblik."}


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Madbank-editor kører på http://localhost:{PORT}  ({len(AUTH_USERS)} bruger(e), se {CREDS_PATH})")
    print("Tryk Ctrl+C for at stoppe.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
