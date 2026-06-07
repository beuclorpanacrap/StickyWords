import os
import json
import difflib
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "usage_stats.json")
VOCAB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expanded_vocabulary.txt")

try:
    from library import WORD_LIST as BASE_WORD_LIST
except ImportError:
    BASE_WORD_LIST = ["hello", "world", "everything", "stickywords"]

# Load extra words from file
extra_words = []
if os.path.exists(VOCAB_FILE):
    with open(VOCAB_FILE, "r", encoding="utf-8") as f:
        extra_words = [line.strip().lower() for line in f if line.strip()]

WORD_LIST = list(dict.fromkeys([w.lower() for w in BASE_WORD_LIST] + extra_words))
WORD_SET  = set(WORD_LIST)

usage_stats = {}

def load_usage_stats():
    global usage_stats
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r") as f:
                usage_stats = json.load(f)
        except Exception:
            usage_stats = {}

def save_usage_stats():
    with open(USAGE_FILE, "w") as f:
        json.dump(usage_stats, f, indent=2)

load_usage_stats()

# ── core routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/predict", methods=["POST"])
def predict():
    data = request.json or {}
    active_word = data.get("word", "").strip().lower()

    if len(active_word) < 2 or active_word in WORD_SET:
        return jsonify({"suggestions": []})

    # 1. Prefix matches
    prefix_matches = [w for w in WORD_LIST if w.startswith(active_word)]

    # 2. Fuzzy matches via difflib (replaces BK-Tree + Levenshtein)
    fuzzy_matches = difflib.get_close_matches(
        active_word, WORD_LIST, n=10, cutoff=0.6
    )

    # 3. Combine, dedupe
    raw = list(dict.fromkeys(prefix_matches + fuzzy_matches))

    # Rank: usage stats first, then exact prefix, then shortest
    usage_bucket = usage_stats.get(active_word, {})

    def ranker(w):
        usage    = usage_bucket.get(w, 0)
        is_prefix = 1 if w.startswith(active_word) else 0
        return (-usage, -is_prefix, len(w))

    raw = sorted(raw, key=ranker)
    return jsonify({"suggestions": raw[:4]})

@app.route("/learn", methods=["POST"])
def learn():
    import datetime
    data     = request.json or {}
    typed    = data.get("typed",    "").lower().strip()
    selected = data.get("selected", "").lower().strip()
    app_name = data.get("app",      "Web Editor").strip()

    if typed and selected:
        # Per-word correction stats
        usage_stats.setdefault(typed, {})
        usage_stats[typed][selected] = usage_stats[typed].get(selected, 0) + 1

        # Per-app correction stats
        app_key = f"__app__{app_name}"
        usage_stats.setdefault(app_key, {})
        usage_stats[app_key][typed] = usage_stats[app_key].get(typed, 0) + 1

        # ── Timeline: bucket by hour (NEW real data) ──────────────────────
        now       = datetime.datetime.now()
        hour_key  = now.strftime("%Y-%m-%dT%H:00")   # e.g. "2025-06-03T14:00"
        tl = usage_stats.setdefault("__timeline__", {})
        tl[hour_key] = tl.get(hour_key, 0) + 1

        save_usage_stats()
        return jsonify({"status": "success"})
    return jsonify({"status": "ignored"}), 400

# ── stats routes ──────────────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def get_stats():
    load_usage_stats()
    total_corrections = 0
    mistakes_list     = []

    for typed_word, target_dict in usage_stats.items():
        if typed_word.startswith("__"):
            continue
        word_total = sum(target_dict.values())
        total_corrections += word_total
        best_fix = max(target_dict, key=target_dict.get) if target_dict else "unknown"
        mistakes_list.append({"typo": typed_word, "fix": best_fix, "count": word_total})

    mistakes_list.sort(key=lambda x: x["count"], reverse=True)
    return jsonify({
        "total_corrections": total_corrections,
        "unique_mistakes":   len(mistakes_list),
        "top_mistakes":      mistakes_list[:5],
    })

@app.route("/api/app_stats", methods=["GET"])
def get_app_stats():
    load_usage_stats()
    app_breakdown = []

    for key, typo_dict in usage_stats.items():
        if not key.startswith("__app__"):
            continue
        app_name  = key[len("__app__"):]
        app_total = sum(typo_dict.values())
        top_typo  = max(typo_dict, key=typo_dict.get) if typo_dict else ""
        app_breakdown.append({"app": app_name, "count": app_total, "top_typo": top_typo})

    app_breakdown.sort(key=lambda x: x["count"], reverse=True)

    grand_total = sum(a["count"] for a in app_breakdown) or 1
    for a in app_breakdown:
        a["pct"] = round(a["count"] / grand_total * 100, 1)

    return jsonify({"apps": app_breakdown[:8]})

@app.route("/api/timeline", methods=["GET"])
def get_timeline():
    """
    Returns real hourly correction counts stored in __timeline__.
    Each key is an ISO hour string: "2025-06-03T14:00".
    The frontend can group these by day or display them as-is.
    Falls back to demo data only when __timeline__ is completely absent.
    """
    import datetime, random
    load_usage_stats()
    timeline = usage_stats.get("__timeline__", {})

    if not timeline:
        # Demo fallback — synthetic per-hour data for the last 48 hours
        random.seed(42)
        now  = datetime.datetime.now()
        demo = {}
        for h in range(47, -1, -1):
            bucket = (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")
            # Realistic bell-curve-ish pattern: busier midday
            hour_of_day = (now - datetime.timedelta(hours=h)).hour
            weight = max(0, 1 - abs(hour_of_day - 13) / 8)
            demo[bucket] = int(random.triangular(0, 10, 10 * weight))
        return jsonify({"timeline": demo, "demo": True})

    return jsonify({"timeline": timeline, "demo": False})

# ── vocabulary / whitelist routes ─────────────────────────────────────────────

@app.route("/api/add_word", methods=["POST"])
def add_word():
    global WORD_LIST, WORD_SET
    data = request.json or {}
    word = data.get("word", "").lower().strip()

    if not word or not word.isalpha():
        return jsonify({"status": "invalid"}), 400
    if word in WORD_SET:
        return jsonify({"status": "already_known"})

    with open(VOCAB_FILE, "a", encoding="utf-8") as f:
        f.write(word + "\n")

    WORD_LIST.append(word)
    WORD_SET.add(word)

    removed = word in usage_stats
    usage_stats.pop(word, None)
    save_usage_stats()

    return jsonify({"status": "added", "word": word, "removed_from_log": removed})

@app.route("/api/ignore_typo", methods=["POST"])
def ignore_typo():
    data = request.json or {}
    word = data.get("word", "").lower().strip()
    if not word:
        return jsonify({"status": "invalid"}), 400

    removed = word in usage_stats
    usage_stats.pop(word, None)
    save_usage_stats()
    return jsonify({"status": "ignored", "word": word, "was_present": removed})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)