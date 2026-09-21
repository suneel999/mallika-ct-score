"""CT Coronary Calcium Score Report generator."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from report_engine import GENERATED_DIR, generate_report, risk_from_score

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

REQUIRED_TEXT = ("name", "id", "ref", "s_date", "r_date", "reportedby")
REQUIRED_NUMBERS = ("age", "score", "lm", "lad", "lcx", "rca")
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg"}


def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _as_float(name: str, required: bool = True):
    raw = (request.form.get(name) or "").strip()
    if raw == "":
        if required:
            raise ValueError(f"{name} is required")
        return 0.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def parse_form():
    missing = [name for name in REQUIRED_TEXT if not (request.form.get(name) or "").strip()]
    if missing:
        raise ValueError("Please fill in all required fields.")

    age_raw = (request.form.get("age") or "").strip()
    try:
        age = int(float(age_raw))
    except ValueError as exc:
        raise ValueError("Age must be a valid positive number") from exc
    if age <= 0 or age > 120:
        raise ValueError("Age must be a valid positive number")

    fields = {
        "name": request.form.get("name", "").strip(),
        "id": request.form.get("id", "").strip(),
        "age": age,
        "ref": request.form.get("ref", "").strip(),
        "s_date": request.form.get("s_date", "").strip(),
        "r_date": request.form.get("r_date", "").strip(),
        "score": _as_float("score"),
        "start": (request.form.get("start") or "").strip(),
        "end": (request.form.get("end") or "").strip(),
        "lm": _as_float("lm"),
        "lad": _as_float("lad"),
        "lcx": _as_float("lcx"),
        "rca": _as_float("rca"),
        "auto_total": request.form.get("auto_total") in ("on", "true", "1", "yes"),
        "reportedby": request.form.get("reportedby", "").strip(),
        "qual": request.form.get("qual", "").strip(),
        "des": request.form.get("des", "").strip(),
        "risk": request.form.get("risk", "").strip(),
    }
    if fields["auto_total"]:
        fields["total"] = fields["lm"] + fields["lad"] + fields["lcx"] + fields["rca"]
    else:
        fields["total"] = _as_float("total")

    if not fields["risk"]:
        fields["risk"] = risk_from_score(fields["score"])
    return fields


def total_warning(fields: dict) -> str | None:
    expected = fields["lm"] + fields["lad"] + fields["lcx"] + fields["rca"]
    if abs(fields["total"] - expected) > 0.009:
        return (
            f"Total score ({fields['total']:.2f}) does not match "
            f"LM + LAD + LCX + RCA ({expected:.2f})."
        )
    return None


@app.route("/")
def index():
    return render_template("form.html")


@app.post("/api/generate")
def api_generate():
    try:
        fields = parse_form()
    except ValueError as exc:
        return _error(str(exc))

    image = request.files.get("image")
    image_file = None
    if image and image.filename:
        suffix = Path(image.filename).suffix.lower()
        if suffix not in ALLOWED_IMAGE_EXT:
            return _error("CT image must be a PNG, JPG, or JPEG file.")
        image_file = image.stream
    else:
        return _error("Please upload a CT coronary calcium image.")

    token = uuid.uuid4().hex
    try:
        result = generate_report(fields, image_file, token)
    except Exception as exc:
        return _error(f"Could not generate the PDF: {exc}", 500)

    warning = total_warning(fields)
    verification = result["verification"]
    if not verification.get("one_page") or not verification.get("is_a4"):
        return jsonify(
            {
                "ok": False,
                "error": (
                    "PDF must be exactly one A4 page. "
                    f"Got {verification.get('page_count')} page(s), "
                    f"{verification.get('width_mm')}×{verification.get('height_mm')} mm."
                ),
                "verification": verification,
            }
        ), 500

    leftovers = verification.get("leftover_placeholders") or verification.get("xml_leftovers")
    if leftovers:
        return jsonify(
            {
                "ok": False,
                "error": f"Unreplaced placeholders remain: {', '.join(leftovers)}",
                "verification": verification,
            }
        ), 500

    return jsonify(
        {
            "ok": True,
            "token": token,
            "filename": result["download_name"],
            "preview_url": f"/preview/{token}",
            "download_url": f"/download/{token}",
            "warning": warning,
            "verification": verification,
        }
    )


def _pdf_for_token(token: str) -> Path | None:
    if not token.isalnum() or len(token) > 64:
        return None
    folder = GENERATED_DIR / token
    if not folder.is_dir():
        return None
    matches = list(folder.glob("*.pdf"))
    return matches[0] if matches else None


@app.get("/preview/<token>")
def preview_pdf(token: str):
    pdf_path = _pdf_for_token(token)
    if pdf_path is None:
        return _error("Preview not found. Generate the report again.", 404)
    return send_file(pdf_path, mimetype="application/pdf")


@app.get("/download/<token>")
def download_pdf(token: str):
    pdf_path = _pdf_for_token(token)
    if pdf_path is None:
        return _error("File not found. Generate the report again.", 404)
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_path.name,
    )


if __name__ == "__main__":
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
