#!/usr/bin/env python3
"""Local testbed server for the Tesla card image submission pipeline.

Serves a simplified upload page and processes submissions locally without
requiring GitHub PATs or API calls.

Usage:
    python3 scripts/local_testbed.py [--port 8080]
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Global: set by --card-repo CLI arg
CARD_REPO = None

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# Upload filenames expected per mode (must match LAYERS in testbed.js)
OFFCHARGE_FILES = [
    "closed.png", "cp.png", "cp_ft.png", "rt.png",
    "front_doors.png", "rear_doors.png", "all_doors.png",
    "top_controls.png", "top_climate.png",
]
ONCHARGE_FILES = [
    "oc_closed.png", "oc_cp_ft.png", "oc_rt.png",
    "oc_front_doors.png", "oc_rear_doors.png", "oc_all_doors.png",
]

# Map form field keys (from testbed.js LAYERS) to expected filenames.
# The browser sends layer.key as the FormData field name.
KEY_TO_FILENAME = {
    "closed": "closed.png",
    "chargeport": "cp.png",
    "frunk": "cp_ft.png",
    "trunk": "rt.png",
    "front_doors": "front_doors.png",
    "rear_doors": "rear_doors.png",
    "all_doors": "all_doors.png",
    "controls": "top_controls.png",
    "climate": "top_climate.png",
    "oc_closed": "oc_closed.png",
    "oc_frunk": "oc_cp_ft.png",
    "oc_trunk": "oc_rt.png",
    "oc_front_doors": "oc_front_doors.png",
    "oc_rear_doors": "oc_rear_doors.png",
    "oc_all_doors": "oc_all_doors.png",
}

# Dev-files: map layer keys to expected filenames inside --dev-dir.
# offcharge files live in {dev_dir}/offcharge/{key}.png
# oncharge files live in {dev_dir}/oncharge/{key}.png
# Dev-files: map layer keys to candidate filenames inside --dev-dir.
# Each key lists filenames to try in order (first match wins).
DEV_FILE_CANDIDATES = {
    "closed":       ["offcharge/closed.png"],
    "chargeport":   ["offcharge/chargeport.png", "offcharge/cp.png"],
    "frunk":        ["offcharge/frunk.png", "offcharge/cp_ft.png"],
    "trunk":        ["offcharge/trunk.png", "offcharge/rt.png"],
    "front_doors":  ["offcharge/front_doors.png", "offcharge/pf_pr.png"],
    "rear_doors":   ["offcharge/rear_doors.png", "offcharge/cp_df_dr.png"],
    "controls":     ["offcharge/controls.png", "offcharge/top_controls.png"],
    "climate":      ["offcharge/climate.png", "offcharge/top_climate.png"],
    "oc_closed":    ["oncharge/oc_closed.png", "oncharge/closed.png"],
    "oc_frunk":     ["oncharge/oc_frunk.png", "oncharge/cp_ft.png"],
    "oc_trunk":     ["oncharge/oc_trunk.png", "oncharge/rt.png"],
    "oc_front_doors": ["oncharge/oc_front_doors.png", "oncharge/pf_pr.png"],
    "oc_rear_doors":  ["oncharge/oc_rear_doors.png", "oncharge/cp_df_dr.png"],
    "all_doors":      ["offcharge/all_doors.png"],
    "oc_all_doors":   ["oncharge/oc_all_doors.png", "oncharge/all_doors.png"],
}

def _is_valid_submission_ref(ref):
    """Check if a ref is a valid submissions branch name."""
    return ref.startswith("submissions/") or ref.startswith("origin/submissions/")


def _load_models_json():
    """Load models.json from the card repo (if configured) or return None."""
    if CARD_REPO:
        models_path = Path(CARD_REPO) / "models.json"
        if models_path.is_file():
            return json.loads(models_path.read_text())
    return None


def _parse_submission_name(dir_name, models_data):
    """Parse a submission directory name into (model, variant, colour).

    Format: {model_id}-{variant_id}-{colour_id}[-{hex_token}]
    Uses models.json for unambiguous parsing since variant IDs contain dots.
    Returns (model, variant, colour) or None if parsing fails.
    """
    if not models_data:
        return None

    for m in models_data["models"]:
        prefix = m["id"] + "-"
        if not dir_name.startswith(prefix):
            continue
        remainder = dir_name[len(prefix):]

        for v in m["variants"]:
            vprefix = v["id"] + "-"
            if not remainder.startswith(vprefix):
                continue
            colour_and_token = remainder[len(vprefix):]

            # Strip trailing -XXXX hex token if present
            import re as _re
            colour = _re.sub(r"-[0-9a-f]{4}$", "", colour_and_token)

            # Verify colour exists in this variant
            colour_ids = [c["id"] for c in v["colours"]]
            if colour in colour_ids:
                return (m["id"], v["id"], colour)

    return None

# Global: set by --dev-dir CLI arg
DEV_DIR = None


# ── Multipart parser ────────────────────────────────────────────────────

def parse_multipart(body, boundary):
    """Parse multipart/form-data body into {field_name: value_or_bytes}.

    Returns a dict where string fields have str values and file fields
    have bytes values.
    """
    result = {}
    # boundary in the body is prefixed with --
    sep = b"--" + boundary.encode("utf-8")
    parts = body.split(sep)

    for part in parts:
        # Skip preamble and closing delimiter
        if not part or part.strip() == b"--" or part.strip() == b"":
            continue

        # Split headers from body at first double CRLF
        if b"\r\n\r\n" in part:
            header_block, file_body = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_block, file_body = part.split(b"\n\n", 1)
        else:
            continue

        # Strip trailing \r\n from body (boundary separator remnant)
        if file_body.endswith(b"\r\n"):
            file_body = file_body[:-2]
        elif file_body.endswith(b"\n"):
            file_body = file_body[:-1]

        headers_str = header_block.decode("utf-8", errors="replace")

        # Extract name from Content-Disposition
        name_match = re.search(r'name="([^"]+)"', headers_str)
        if not name_match:
            continue
        name = name_match.group(1)

        # Check if it's a file upload (has filename)
        filename_match = re.search(r'filename="([^"]*)"', headers_str)
        if filename_match:
            # File field — store raw bytes
            result[name] = file_body
        else:
            # Text field — decode as string
            result[name] = file_body.decode("utf-8", errors="replace")

    return result


class TestbedHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the local testbed."""

    def log_message(self, format, *args):
        """Override to prefix log with [testbed]."""
        sys.stderr.write("[testbed] %s - %s\n" %
                         (self.address_string(), format % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._redirect("/testbed.html")
            return

        if path == "/api/models":
            self._serve_models()
            return

        if path == "/api/status":
            self._serve_status()
            return

        if path == "/api/branches":
            self._serve_branches()
            return

        if path == "/api/dev-files":
            self._serve_dev_files()
            return

        if path.startswith("/api/dev-file/"):
            self._serve_dev_file(path[len("/api/dev-file/"):])
            return

        if path.startswith("/submissions/"):
            ref = query.get("ref", [None])[0]
            self._serve_submission_file(path, ref=ref)
            return

        # Serve static files from docs/
        self._serve_static(path)

    def do_POST(self):
        if self.path == "/api/submit":
            self._handle_submit()
            return

        if self.path == "/api/promote":
            self._handle_promote()
            return

        if self.path == "/api/reset-status":
            self._handle_reset_status()
            return

        self._json_error(404, "Not found")

    def do_DELETE(self):
        if self.path.startswith("/api/branch/"):
            self._handle_delete_branch()
            return

        self._json_error(404, "Not found")

    def _handle_delete_branch(self):
        """Delete a submission directory (and git branch if it exists)."""
        branch_name = unquote(self.path[len("/api/branch/"):])
        if not branch_name.startswith("submissions/"):
            self._json_error(400, "Invalid branch name")
            return

        dir_name = branch_name[len("submissions/"):]
        submission_dir = REPO_ROOT / "submissions" / dir_name

        # Remove submission directory
        dir_deleted = False
        if submission_dir.is_dir():
            shutil.rmtree(submission_dir)
            dir_deleted = True

        # Delete git branch (local)
        branch_result = subprocess.run(
            ["git", "branch", "-D", branch_name],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        branch_deleted = branch_result.returncode == 0

        if dir_deleted or branch_deleted:
            self._json_response({"success": True})
        else:
            self._json_error(404, f"Submission '{dir_name}' not found")

    # ── Static file serving ──────────────────────────────────────────────

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _serve_static(self, url_path):
        # Map URL path to docs/ directory
        rel = url_path.lstrip("/")
        file_path = DOCS_DIR / rel

        # Security: don't allow path traversal
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(DOCS_DIR.resolve())):
                self._json_error(403, "Forbidden")
                return
        except (ValueError, OSError):
            self._json_error(403, "Forbidden")
            return

        if not file_path.is_file():
            self._json_error(404, f"Not found: {url_path}")
            return

        suffix = file_path.suffix.lower()
        content_type = MIME_TYPES.get(suffix, "application/octet-stream")

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ── API: models ─────────────────────────────────────────────────────

    def _serve_models(self):
        models = _load_models_json()
        if models is None:
            self._json_error(404,
                "models.json not found. Ensure --card-repo points to "
                "a card repo with models.json")
            return
        self._json_response(models)

    # ── API: status ──────────────────────────────────────────────────────

    def _serve_status(self):
        status_path = DOCS_DIR / "status.json"
        if not status_path.is_file():
            # Fallback to repo root
            status_path = REPO_ROOT / "status.json"
        if not status_path.is_file():
            self._json_error(404, "status.json not found")
            return

        data = json.loads(status_path.read_text())
        self._json_response(data)

    # ── API: branches ─────────────────────────────────────────────────────

    def _serve_branches(self):
        """List submissions from git branches and local directories."""
        # Collect entries as {submissions_name: source}
        # source = "local" (filesystem dir) or the git ref string
        entries = {}

        # Scan local submission directories that have processed output
        submissions_root = REPO_ROOT / "submissions"
        if submissions_root.is_dir():
            for d in sorted(submissions_root.iterdir()):
                if d.is_dir() and (d / "processed").is_dir():
                    name = f"submissions/{d.name}"
                    entries[name] = "local"

        # Scan local git branches
        try:
            result = subprocess.run(
                ["git", "branch", "--list", "submissions/*",
                 "--format=%(refname:short)"],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )
            for b in result.stdout.strip().split("\n"):
                b = b.strip()
                if b and b not in entries:
                    entries[b] = b  # ref = branch name
        except Exception:
            pass

        # Scan remote-tracking branches
        try:
            result = subprocess.run(
                ["git", "branch", "-r", "--list", "origin/submissions/*",
                 "--format=%(refname:short)"],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )
            for b in result.stdout.strip().split("\n"):
                b = b.strip()
                if b and b.startswith("origin/"):
                    local_name = b[len("origin/"):]
                    if local_name not in entries:
                        entries[local_name] = b  # ref = origin/...
        except Exception:
            pass

        # Load models.json for parsing and labels
        models_data = _load_models_json()

        branches = []
        for name in sorted(entries.keys()):
            source = entries[name]

            # Parse submissions/{model}-{variant}-{colour}[-{token}]
            suffix = name[len("submissions/"):]
            parsed = _parse_submission_name(suffix, models_data)
            if not parsed:
                continue

            model, variant, colour = parsed

            # Build human-readable label from models.json
            label = suffix  # fallback
            if models_data:
                m = next((x for x in models_data["models"] if x["id"] == model), None)
                v = next((x for x in m["variants"] if x["id"] == variant), None) if m else None
                c = next((x for x in v["colours"] if x["id"] == colour), None) if v else None
                if m and v and c:
                    label = f"{m['name']} {v['label']} \u2014 {c['name']}"

            branches.append({
                "name": name,
                "model": model,
                "variant": variant,
                "colour": colour,
                "label": label,
                "source": source,
            })

        self._json_response({"branches": branches})

    # ── API: dev-files (auto-populate from local directory) ───────────────

    def _serve_dev_files(self):
        """Return list of available dev files for auto-populate."""
        if not DEV_DIR:
            self._json_error(404, "No --dev-dir configured")
            return

        available = {}
        for key, candidates in DEV_FILE_CANDIDATES.items():
            for rel_path in candidates:
                if (DEV_DIR / rel_path).is_file():
                    available[key] = rel_path
                    break

        self._json_response({"available": available})

    def _serve_dev_file(self, key):
        """Serve a single dev file by layer key."""
        if not DEV_DIR:
            self._json_error(404, "No --dev-dir configured")
            return

        candidates = DEV_FILE_CANDIDATES.get(key)
        if not candidates:
            self._json_error(404, f"Unknown layer key: {key}")
            return

        full = None
        for rel_path in candidates:
            candidate = DEV_DIR / rel_path
            if candidate.is_file():
                full = candidate
                break

        if not full:
            self._json_error(404, f"No dev file found for: {key}")
            return

        data = full.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Submission file serving ──────────────────────────────────────────

    def _serve_submission_file(self, url_path, ref=None):
        """Serve processed files from submissions/ directory or git ref."""
        rel = url_path.lstrip("/")

        # Security: validate ref is a submissions branch
        if ref and not _is_valid_submission_ref(ref):
            self._json_error(403, "Forbidden: invalid ref")
            return

        # Security: no path traversal
        if ".." in rel:
            self._json_error(403, "Forbidden")
            return

        suffix = Path(rel).suffix.lower()
        content_type = MIME_TYPES.get(suffix, "application/octet-stream")

        if ref:
            # Read file from git ref
            data = _git_read_file(ref, rel)
            if data is None:
                self._json_error(404, f"Not found in {ref}: {rel}")
                return
        else:
            # Read from filesystem
            file_path = (REPO_ROOT / rel).resolve()
            submissions_root = (REPO_ROOT / "submissions").resolve()
            if not str(file_path).startswith(str(submissions_root)):
                self._json_error(403, "Forbidden")
                return
            if not file_path.is_file():
                self._json_error(404, f"Not found: {url_path}")
                return
            data = file_path.read_bytes()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ── API: promote ─────────────────────────────────────────────────────

    def _handle_promote(self):
        """Copy processed images to the card repo and mark status complete."""
        body = self._read_json_body()
        if body is None:
            return

        model = body.get("model", "")
        variant = body.get("variant", "")
        colour = body.get("colour", "")
        ref = body.get("ref", None)

        if not model or not variant or not colour:
            self._json_error(400, "Missing model, variant, or colour")
            return

        # Security: validate ref
        if ref and not _is_valid_submission_ref(ref):
            self._json_error(403, "Forbidden: invalid ref")
            return

        if not CARD_REPO:
            self._json_error(400,
                "No --card-repo configured. "
                "Restart with: --card-repo ../homeassistant-fe-tesla")
            return

        card_repo = Path(CARD_REPO)
        if not card_repo.is_dir():
            self._json_error(400, f"Card repo not found: {card_repo}")
            return

        # Card path = {model}/{variant}/{colour}
        card_path = f"{model}/{variant}/{colour}"

        submission_name = f"{model}-{variant}-{colour}"
        processed_prefix = f"submissions/{submission_name}/processed"

        if ref:
            # Verify the ref has processed files by checking for base.png
            test = _git_read_file(ref, f"{processed_prefix}/offcharge/base.png")
            if test is None:
                self._json_error(404,
                    f"No processed files found in {ref}")
                return
        else:
            processed = REPO_ROOT / "submissions" / submission_name / "processed"
            if not processed.is_dir():
                self._json_error(404, f"Processed dir not found: {processed}")
                return

        # Build target directory
        target = card_repo / "images" / "models" / card_path
        target_overlays = target / "overlays"
        target.mkdir(parents=True, exist_ok=True)
        target_overlays.mkdir(parents=True, exist_ok=True)

        # File copy mapping: (source_relative_to_processed, target_relative_to_colour_dir)
        copy_map = [
            ("offcharge/base.png", "base.png"),
            ("offcharge/chargeport-open.png", "chargeport-open.png"),
            ("offcharge/frunk-open.png", "frunk-open.png"),
            ("offcharge/controls-bg.png", "controls-bg.png"),
            ("offcharge/climate-bg.png", "climate-bg.png"),
            ("oncharge/controls-bg.png", "controls-bg-charging.png"),
            ("oncharge/climate-bg.png", "climate-bg-charging.png"),
        ]

        file_count = 0

        if ref:
            # Copy from git ref
            for src_rel, dst_rel in copy_map:
                data = _git_read_file(ref, f"{processed_prefix}/{src_rel}")
                if data:
                    (target / dst_rel).write_bytes(data)
                    file_count += 1

            # Copy overlay files from git
            for mode_dir in ["offcharge/overlays", "oncharge/overlays"]:
                git_dir = f"{processed_prefix}/{mode_dir}"
                files = _git_list_tree(ref, git_dir)
                for fname in files:
                    data = _git_read_file(ref, f"{git_dir}/{fname}")
                    if data:
                        (target_overlays / fname).write_bytes(data)
                        file_count += 1
        else:
            processed = REPO_ROOT / "submissions" / submission_name / "processed"

            # Copy root-level files
            for src_rel, dst_rel in copy_map:
                src = processed / src_rel
                if src.is_file():
                    shutil.copy2(src, target / dst_rel)
                    file_count += 1

            # Copy all overlay files from offcharge and oncharge
            for mode_dir in ["offcharge/overlays", "oncharge/overlays"]:
                overlay_src = processed / mode_dir
                if overlay_src.is_dir():
                    for f in overlay_src.iterdir():
                        if f.is_file():
                            shutil.copy2(f, target_overlays / f.name)
                            file_count += 1

        # Update status.json → complete (flat format)
        status_key = f"{model}/{variant}/{colour}"
        status_path = DOCS_DIR / "status.json"
        if not status_path.is_file():
            status_path = REPO_ROOT / "status.json"
        status = json.loads(status_path.read_text()) if status_path.is_file() else {}
        status[status_key] = {"status": "complete"}
        status_out = DOCS_DIR / "status.json"
        status_out.write_text(json.dumps(status, indent=2) + "\n")

        # Update hasImages in models.json
        models_data = _load_models_json()
        if models_data:
            for m in models_data["models"]:
                if m["id"] != model:
                    continue
                for v in m["variants"]:
                    if v["id"] != variant:
                        continue
                    for c in v["colours"]:
                        if c["id"] == colour:
                            c["hasImages"] = True
            models_path = Path(CARD_REPO) / "models.json"
            models_path.write_text(json.dumps(models_data, indent=2) + "\n")

        self._json_response({
            "success": True,
            "target_dir": str(target),
            "file_count": file_count,
        })

    # ── API: reset-status ────────────────────────────────────────────────

    def _handle_reset_status(self):
        """Reset a colour status back to available (dev tool)."""
        body = self._read_json_body()
        if body is None:
            return

        model = body.get("model", "")
        variant = body.get("variant", "")
        colour = body.get("colour", "")

        if not model or not variant or not colour:
            self._json_error(400, "Missing model, variant, or colour")
            return

        status_path = DOCS_DIR / "status.json"
        if not status_path.is_file():
            status_path = REPO_ROOT / "status.json"
        if not status_path.is_file():
            self._json_error(404, "status.json not found")
            return

        status = json.loads(status_path.read_text())

        # Flat format: remove the key to reset to implicitly available
        status_key = f"{model}/{variant}/{colour}"
        status.pop(status_key, None)

        status_out = DOCS_DIR / "status.json"
        status_out.write_text(json.dumps(status, indent=2) + "\n")

        self._json_response({"success": True})

    # ── API: submit ──────────────────────────────────────────────────────

    def _handle_submit(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_error(400, "Expected multipart/form-data")
            return

        # Extract boundary from Content-Type header
        boundary_match = re.search(r"boundary=(.+)", content_type)
        if not boundary_match:
            self._json_error(400, "No boundary in Content-Type")
            return
        boundary = boundary_match.group(1).strip()

        # Read full request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_error(400, "Empty request body")
            return

        body = self.rfile.read(content_length)

        # Parse multipart data
        try:
            fields = parse_multipart(body, boundary)
        except Exception as e:
            self._json_error(400, f"Failed to parse form data: {e}")
            return

        self.log_message("Body size: %d bytes, boundary: %r", len(body), boundary)
        self.log_message("Parsed %d form fields: %s",
                         len(fields),
                         ", ".join(f"{k}({'file' if isinstance(v, bytes) else 'text'}:{len(v)})"
                                   for k, v in fields.items()))

        # Extract metadata fields
        model = fields.get("model", "")
        variant = fields.get("variant", "")
        colour = fields.get("colour", "")

        if not model or not variant or not colour:
            self._json_error(400, "Missing model, variant, or colour")
            return

        # Build branch name and submission directory
        branch_name = f"submissions/{model}-{variant}-{colour}"
        submission_dir = REPO_ROOT / "submissions" / f"{model}-{variant}-{colour}"

        # Check if submission directory already exists
        if submission_dir.is_dir():
            self._json_error(409,
                f"Submission directory already exists: {submission_dir.name}. "
                "Delete it first if you want to resubmit.")
            return

        # Start streaming response — progress events as NDJSON
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send_progress(step, message, pct, **extra):
            """Send a progress event as a chunked NDJSON line."""
            event = {"type": "progress", "step": step,
                     "message": message, "pct": pct}
            event.update(extra)
            line = json.dumps(event) + "\n"
            chunk = f"{len(line.encode()):x}\r\n{line}\r\n"
            self.wfile.write(chunk.encode())
            self.wfile.flush()

        def send_log(text):
            """Send a log line."""
            line = json.dumps({"type": "log", "text": text}) + "\n"
            chunk = f"{len(line.encode()):x}\r\n{line}\r\n"
            self.wfile.write(chunk.encode())
            self.wfile.flush()

        def send_result(data):
            """Send the final result and close the chunked stream."""
            data["type"] = "result"
            line = json.dumps(data) + "\n"
            chunk = f"{len(line.encode()):x}\r\n{line}\r\n"
            self.wfile.write(chunk.encode())
            # Chunked terminator
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        # Save uploaded files (form field names are layer keys from testbed.js)
        send_progress("save", "Saving uploaded files...", 5)
        submission_dir.mkdir(parents=True, exist_ok=True)

        # All-doors screenshots are optional (improve combined overlays)
        OPTIONAL_KEYS = {"all_doors", "oc_all_doors"}

        saved_files = []
        missing_files = []
        for key, filename in KEY_TO_FILENAME.items():
            if key in fields and isinstance(fields[key], bytes):
                dest = submission_dir / filename
                dest.write_bytes(fields[key])
                saved_files.append(filename)
            elif key not in OPTIONAL_KEYS:
                missing_files.append(f"{key} ({filename})")

        if missing_files:
            send_result({"success": False,
                         "error": f"Missing files: {', '.join(missing_files)}"})
            return

        self.log_message("Saved %d files to %s", len(saved_files),
                         submission_dir)

        # Split input files into offcharge/oncharge subdirectories for processing
        offcharge_input = submission_dir / "offcharge"
        oncharge_input = submission_dir / "oncharge"
        offcharge_input.mkdir(exist_ok=True)
        oncharge_input.mkdir(exist_ok=True)

        for f in OFFCHARGE_FILES:
            src = submission_dir / f
            if src.exists():
                (offcharge_input / f).write_bytes(src.read_bytes())

        for f in ONCHARGE_FILES:
            src = submission_dir / f
            if src.exists():
                data = src.read_bytes()
                # Keep oc_ prefixed name (patterns try oc_* first)
                (oncharge_input / f).write_bytes(data)
                # Also copy with oc_ stripped (unprefixed fallback patterns)
                (oncharge_input / f.replace("oc_", "")).write_bytes(data)

        # Run validation and processing BEFORE git (checkout would wipe files)
        processed_dir = submission_dir / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        offcharge_output = processed_dir / "offcharge"
        oncharge_output = processed_dir / "oncharge"

        pipeline_log = []

        # Validate the full submission (all 13 files)
        send_progress("validate", "Validating submission...", 15)
        pipeline_log.append("=== Validating submission ===")
        val_result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_submission.py"),
             "--submission-dir", str(submission_dir)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        pipeline_log.append(val_result.stdout)
        if val_result.stderr:
            pipeline_log.append(val_result.stderr)

        # Run processing for each mode
        send_progress("offcharge", "Processing offcharge images...", 25)
        offcharge_ok = self._run_pipeline(
            offcharge_input, offcharge_output, "offcharge", pipeline_log,
            send_log)
        send_progress("oncharge", "Processing oncharge images...", 60)
        oncharge_ok = self._run_pipeline(
            oncharge_input, oncharge_output, "oncharge", pipeline_log,
            send_log)

        send_progress("done", "Finishing up...", 95)
        self.log_message("Processed submission: %s", submission_dir.name)

        send_result({
            "success": offcharge_ok and oncharge_ok,
            "branch": branch_name,
            "submission_dir": str(submission_dir),
            "offcharge_output": str(offcharge_output),
            "oncharge_output": str(oncharge_output),
            "log": pipeline_log,
        })

    def _run_pipeline(self, input_dir, output_dir, mode, log,
                      send_log=None):
        """Run process pipeline for one mode. Returns True on success."""
        script = str(SCRIPTS_DIR / "process_screenshots.py")

        log.append(f"=== Processing {mode} ===")

        # Stream stdout line-by-line for live progress
        proc = subprocess.Popen(
            [sys.executable, "-u", script,
             "--input-dir", str(input_dir),
             "--output-dir", str(output_dir),
             "--mode", mode,
             "--generate-overlays",
             "--full-res",
             "--verbose"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(REPO_ROOT),
        )

        stdout_lines = []
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            stdout_lines.append(stripped)
            if send_log and stripped.strip():
                send_log(stripped)

        stderr = proc.stderr.read()
        proc.wait()

        log.append("\n".join(stdout_lines))
        if stderr:
            log.append(stderr)

        success = proc.returncode == 0
        log.append(f"=== {mode} {'OK' if success else 'FAILED'} ===")
        return success

    # ── Helpers ───────────────────────────────────────────────────────────

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status, message):
        self._json_response({"error": message}, status=status)

    def _read_json_body(self):
        """Read and parse JSON request body. Returns dict or None on error."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_error(400, "Empty request body")
            return None
        try:
            body = json.loads(self.rfile.read(content_length))
            return body
        except (json.JSONDecodeError, ValueError) as e:
            self._json_error(400, f"Invalid JSON: {e}")
            return None


def _git_current_branch():
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _git_run(cmd):
    """Run a git command, raising on failure."""
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(REPO_ROOT),
        check=True,
    )


def _git_read_file(ref, path):
    """Read a file from a git ref via `git show ref:path`. Returns bytes or None."""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, cwd=str(REPO_ROOT),
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except Exception:
        return None


def _git_list_tree(ref, dir_path):
    """List files in a directory at a git ref. Returns list of filenames."""
    try:
        result = subprocess.run(
            ["git", "ls-tree", "--name-only", f"{ref}:{dir_path}"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().split("\n") if f]
        return []
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Local testbed for Tesla card image submission pipeline")
    parser.add_argument("--port", type=int, default=8080,
                        help="Port to serve on (default: 8080)")
    parser.add_argument("--dev-dir", type=str, default=None,
                        help="Path to local screenshots directory for auto-populate "
                             "(expects offcharge/ and oncharge/ subdirs)")
    parser.add_argument("--card-repo", type=str, default=None,
                        help="Path to homeassistant-fe-tesla card repo "
                             "(default: ../homeassistant-fe-tesla)")
    args = parser.parse_args()

    global DEV_DIR, CARD_REPO
    if args.dev_dir:
        DEV_DIR = Path(args.dev_dir).resolve()
        if not DEV_DIR.is_dir():
            print(f"Error: --dev-dir '{DEV_DIR}' is not a directory")
            sys.exit(1)

    # Resolve card repo path
    card_repo_path = args.card_repo or str(REPO_ROOT.parent / "homeassistant-fe-tesla")
    resolved = Path(card_repo_path).resolve()
    if resolved.is_dir():
        CARD_REPO = resolved

    server = HTTPServer(("localhost", args.port), TestbedHandler)
    print(f"Tesla Card Local Testbed")
    print(f"  Serving on http://localhost:{args.port}")
    print(f"  Repo root: {REPO_ROOT}")
    if DEV_DIR:
        print(f"  Dev dir:   {DEV_DIR}")
    if CARD_REPO:
        print(f"  Card repo: {CARD_REPO}")
    else:
        print(f"  Card repo: not found (promote disabled)")
    print(f"  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
