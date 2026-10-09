from flask import Flask, render_template, request, Response, jsonify
import csv
import io
import re
import os
import json
from pathlib import Path
from datetime import datetime
import threading

app = Flask(__name__)

# Configuration
DATA_DIR = "data"
CACHE_FILE = ".cache.json"

# Créer le dossier data s'il n'existe pas
os.makedirs(DATA_DIR, exist_ok=True)

# Cache global
cache = {"entries": [], "files": {}, "last_sync": None}
sync_lock = threading.Lock()


def extract_ips(text: str):
    """Extrait les IPs du texte"""
    return re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", text)


def normalize_name(raw_name: str):
    """Normalise les noms"""
    return raw_name.strip().strip("'\"[]{}()")


def parse_line(line: str):
    """Parse une ligne du fichier"""
    line = line.strip()
    if not line or line.startswith("#"):
        return []

    # Format: Name : ['ip1', 'ip2']
    match = re.match(r"^(.+?)\s*[:=]\s*\[(.*?)\]\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        ips = extract_ips(match.group(2))
        return [(name, ip) for ip in ips] if ips else []

    # Format: Name : ip
    match = re.match(r"^(.+?)\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        ip = match.group(2)
        return [(name, ip)]

    # Format: Name ip
    match = re.match(r"^([^\s:]+)\s+(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        return [(name, match.group(2))]

    # Format: Name | ip
    match = re.match(r"^([^|]+?)\s*\|\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if match:
        name = normalize_name(match.group(1))
        return [(name, match.group(2))]

    return []


def parse_file(filepath: str):
    """Parse un fichier complet"""
    entries = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                for name, ip in parse_line(line):
                    entries.append({
                        "name": name,
                        "ip": ip,
                        "file": os.path.basename(filepath),
                        "line": line_no,
                        "raw": line.strip(),
                    })
    except Exception as e:
        print(f"Erreur en lisant {filepath}: {e}")
    return entries


def sync_all_files():
    """Synchronise tous les fichiers .txt du dossier data"""
    global cache
    
    with sync_lock:
        entries = []
        files = {}
        
        # Chercher tous les fichiers .txt
        txt_files = list(Path(DATA_DIR).glob("**/*.txt"))
        
        for filepath in txt_files:
            file_key = str(filepath.relative_to(DATA_DIR))
            file_entries = parse_file(str(filepath))
            entries.extend(file_entries)
            files[file_key] = {
                "path": file_key,
                "count": len(file_entries),
                "size": os.path.getsize(filepath),
                "modified": os.path.getmtime(filepath),
            }
        
        cache["entries"] = entries
        cache["files"] = files
        cache["last_sync"] = datetime.now().isoformat()
        
        # Sauvegarder le cache
        save_cache()
        
        return len(entries), len(files)


def save_cache():
    """Sauvegarde le cache sur disque"""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2, default=str)
    except Exception as e:
        print(f"Erreur en sauvegardant le cache: {e}")


def load_cache():
    """Charge le cache depuis le disque"""
    global cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                cache = json.load(f)
                return True
    except Exception as e:
        print(f"Erreur en chargeant le cache: {e}")
    return False


def search_entries(query: str, file_filter: str = None):
    """Recherche dans le cache"""
    q = query.strip().lower()
    results = []
    
    for entry in cache["entries"]:
        # Filtrer par fichier si spécifié
        if file_filter and entry["file"] != file_filter:
            continue
        
        # Si pas de query, retourner tout (limité)
        if not q:
            results.append(entry)
        elif q in entry["name"].lower() or q in entry["ip"]:
            results.append(entry)
    
    # Limiter à 1000 résultats
    return results[:1000]


@app.route("/")
def index():
    """Page principale"""
    files = cache.get("files", {})
    total_entries = len(cache.get("entries", []))
    last_sync = cache.get("last_sync", "Jamais")
    
    return render_template(
        "index.html",
        files=files,
        total_entries=total_entries,
        last_sync=last_sync,
    )


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Endpoint pour synchroniser les fichiers"""
    try:
        total, files = sync_all_files()
        return jsonify({
            "success": True,
            "entries": total,
            "files": files,
            "timestamp": cache["last_sync"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/search")
def api_search():
    """Endpoint de recherche"""
    query = request.args.get("q", "").strip()
    file_filter = request.args.get("file", None)
    
    if len(query) < 1 and not file_filter:
        return jsonify({"results": [], "count": 0})
    
    results = search_entries(query, file_filter)
    
    return jsonify({
        "results": results,
        "count": len(results),
        "query": query,
        "file": file_filter,
    })


@app.route("/api/export")
def api_export():
    """Export des résultats en CSV"""
    query = request.args.get("q", "").strip()
    file_filter = request.args.get("file", None)
    
    results = search_entries(query, file_filter)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Pseudo", "IP", "Fichier", "Ligne", "Texte brut"])
    
    for item in results:
        writer.writerow([
            item["name"],
            item["ip"],
            item["file"],
            item["line"],
            item["raw"],
        ])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=results.csv"}
    )


if __name__ == "__main__":
    # Charger le cache existant
    load_cache()
    
    # Première synchronisation
    print("Synchronisation initiale...")
    total, files = sync_all_files()
    print(f"✓ {total} entrées trouvées dans {files} fichiers")
    
    # Lancer l'app
    app.run(debug=True, host="0.0.0.0", port=5000)
