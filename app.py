"""Render-ready Flask entrypoint for the Lab Google web app."""

from pathlib import Path

from flask import Flask, jsonify, request

from server import search_labs


ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="")


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/search")
def search():
    payload = request.get_json(silent=True) or {}
    try:
        response = search_labs(
            str(payload.get("query") or ""),
            int(payload.get("limit") or 8),
            bool(payload.get("enrich", True)),
            bool(payload.get("prestigious", False)),
        )
        return jsonify(response)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # Keep upstream source failures readable in the UI.
        return jsonify({"error": f"Search failed: {exc.__class__.__name__}"}), 502
