#!/usr/bin/env python3
"""Local web editor for madbank.json — no external dependencies, stdlib only.

Serves a small UI to add/edit/delete dishes, plus a "Gem og synkroniser"
action that commits and pushes madbank.json to GitHub (same repo the
tilfoej-ret.sh / hent-liste.sh scripts already work against).

Protected with HTTP Basic Auth so it's safe to port-forward — credentials
come from madbank-editor-credentials.local (sibling of this repo, one level
up from agentclaude/m3numember-pages).
"""
import base64
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MADBANK_PATH = os.path.join(REPO_DIR, "madbank.json")
CREDS_PATH = os.path.expanduser("~/agentclaude/madbank-editor-credentials.local")
GROK_BIN = os.path.expanduser("~/.grok/bin/grok")
PORT = 8420


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


def validate_meals(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("meals"), list):
        raise ValueError("Forkert format: forventede {\"meals\": [...]}")
    cleaned = []
    seen_names = set()
    for i, meal in enumerate(payload["meals"]):
        if not isinstance(meal, dict):
            raise ValueError(f"Ret #{i + 1} er ikke et gyldigt objekt")
        name = str(meal.get("name", "")).strip()
        if not name:
            raise ValueError(f"Ret #{i + 1} mangler et navn")
        key = name.lower()
        if key in seen_names:
            raise ValueError(f'"{name}" findes allerede — navne skal være unikke')
        seen_names.add(key)
        recipe_url = str(meal.get("recipeURL", "")).strip()
        items = [str(item).strip() for item in meal.get("items", []) if str(item).strip()]
        cleaned.append({"name": name, "recipeURL": recipe_url, "items": items})
    return {"meals": cleaned}


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
            with open(MADBANK_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self._send_json(200, data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        if not self._check_auth():
            return self._require_auth()
        if self.path != "/api/meals":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            cleaned = validate_meals(payload)
        except (json.JSONDecodeError, ValueError) as e:
            return self._send_json(400, {"error": str(e)})
        with open(MADBANK_PATH, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, ensure_ascii=False)
            f.write("\n")
        self._send_json(200, {"ok": True, "count": len(cleaned["meals"])})

    def do_POST(self):
        if not self._check_auth():
            return self._require_auth()
        if self.path == "/api/sync":
            return self._send_json(200, self._run_sync())
        if self.path == "/api/suggest-items":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                name = json.loads(raw).get("name", "").strip()
            except json.JSONDecodeError:
                name = ""
            if not name:
                return self._send_json(400, {"error": "Mangler navn på retten"})
            return self._send_json(200, self._suggest_items(name))
        self.send_response(404)
        self.end_headers()

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

    def _run_sync(self):
        def run(*args):
            return subprocess.run(
                args, cwd=REPO_DIR, capture_output=True, text=True, timeout=30
            )

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
