from flask import Flask, render_template, request, Response, jsonify
import csv
import io
import re
import os
import json
from pathlib import Path
from datetime import datetime

app = Flask(__name__)

DATA_DIR = "data"
CACHE_FILE = ".cache.json"

os.makedirs(DATA_DIR, exist_ok=True)

cache = {"entries": [], "files": {}, "last_sync": None}


def extract_ips(text: str):
    return re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", text)


def normalize_name(raw_name: str):
    return raw_name.strip().strip("'\"[]{}()")


def parse_line(line: str):
    line = line.strip()
    if not line or line.startswith("#"):
        return []

    match = re.match(r"^(.+?)\s*[:=]\s*\[(.*?)\]\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        ips = extract_ips(match.group(2))
        return [(name, ip) for ip in ips] if ips else []

    match = re.match(r"^(.+?)\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        return [(name, match.group(2))]

    match = re.match(r"^([^\s:]+)\s+(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        return [(name, match.group(2))]

    match = re.match(r"^([^|]+?)\s*\|\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        return [(name, match.group(2))]

    return []


def parse_file(filepath: str):
    entries = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                for name, ip in parse_line(line):
                    entries.append({
                        "name": name,
                        "ip": ip,
                        "file": os.path.basename(filepath),
                        "full_file": str(Path(filepath).relative_to(Path.cwd())),
                        "line": line_no,
                        "raw": line.strip(),
                    })
    except Exception as e:
        print(f"Erreur lecture {filepath}: {e}")
    return entries


def sync_all_files():
    global cache
    entries = []
    files = {}

    txt_files = list(Path(DATA_DIR).glob("**/*.txt"))
    for filepath in txt_files:
        relative = str(filepath.relative_to(Path.cwd()))
        file_entries = parse_file(str(filepath))
        entries.extend(file_entries)
        files[relative] = {
            "path": relative,
            "name": filepath.name,
            "count": len(file_entries),
            "size": filepath.stat().st_size if filepath.exists() else 0,
            "modified": datetime.fromtimestamp(filepath.stat().st_mtime).isoformat(),
        }

    cache["entries"] = entries
    cache["files"] = files
    cache["last_sync"] = datetime.now().isoformat()
    save_cache()
    return len(entries), len(files)


def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"Cache save error: {e}")


def load_cache():
    global cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
    except Exception as e:
        print(f"Cache load error: {e}")


def search_entries(query: str, file_filter: str = None):
    q = query.strip().lower()
    rows = []
    for item in cache.get("entries", []):
        if file_filter and item["full_file"] != file_filter:
            continue
        if not q:
            rows.append(item)
        elif q in item["name"].lower() or q in item["ip"] or q in item["file"].lower():
            rows.append(item)
    return rows[:1500]


@app.route("/")
def index():
    return render_template(
        "index.html",
        files=cache.get("files", {}),
        total_entries=len(cache.get("entries", [])),
        last_sync=cache.get("last_sync", "Jamais"),
    )


@app.route("/api/sync", methods=["POST"])
def api_sync():
    try:
        total, count = sync_all_files()
        return jsonify({
            "success": True,
            "entries": total,
            "files": count,
            "timestamp": cache["last_sync"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    file_filter = request.args.get("file", "").strip() or None
    rows = search_entries(query, file_filter)
    return jsonify({"results": rows, "count": len(rows)})


@app.route("/api/export")
def api_export():
    query = request.args.get("q", "").strip()
    file_filter = request.args.get("file", "").strip() or None
    rows = search_entries(query, file_filter)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Pseudo", "IP", "Fichier", "Ligne", "Texte"])
    for item in rows:
        writer.writerow([item["name"], item["ip"], item["file"], item["line"], item["raw"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=results.csv"})


if __name__ == "__main__":
    load_cache()
    sync_all_files()
    app.run(debug=True, host="0.0.0.0", port=5000)
