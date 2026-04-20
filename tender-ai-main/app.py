import os
import json
import tempfile
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

load_dotenv()  # loads .env for local development

from auth import (
    register_user, login_user,
    get_company_profile, save_company_profile,
    save_tender_analysis, get_tender_history,
    get_dashboard_stats
)
from analyzer import extract_text_from_pdf, extract_questions, analyze_tender, format_pages_for_prompt

app = Flask(__name__)

# ── Secret key ────────────────────────────────────────────────
# SECRET_KEY must be set as an environment variable on Render.
# The fallback below is intentionally loud so a missing env var
# is obvious in logs rather than silently using a weak key.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    import warnings
    warnings.warn(
        "SECRET_KEY environment variable is not set. "
        "Using an insecure fallback — set SECRET_KEY on Render immediately.",
        stacklevel=2
    )
    _secret = "tender-ai-secret-2024-INSECURE-FALLBACK"
app.secret_key = _secret


# ── Helper ────────────────────────────────────────────────────
def logged_in():
    return "user_id" in session

def require_login():
    if not logged_in():
        flash("Please login to continue.", "error")
        return redirect(url_for("login"))
    return None


# ── Public pages ──────────────────────────────────────────────
@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")


# ── Auth ──────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("register.html")

        result = register_user(email, password)

        if result["success"]:
            user_id = result["user"]["id"]
            # Save company profile from registration form
            profile_data = {
                "company_name": request.form.get("company_name", ""),
                "registration_number": request.form.get("registration_number", ""),
                "pan_number": request.form.get("pan_number", ""),
                "turnover": request.form.get("turnover", 0),
                "experience": request.form.get("experience", 0),
                "domain": request.form.get("domain", ""),
                "sub_domains": request.form.get("sub_domains", "").split(","),
                "employee_count": request.form.get("employee_count", 0),
                "certifications": request.form.get("certifications", ""),
                "address": request.form.get("address", ""),
                "phone": request.form.get("phone", ""),
                "company_email": request.form.get("company_email", email),
            }
            save_company_profile(user_id, profile_data)

            session["user_id"] = user_id
            session["user_email"] = email
            flash("Account created successfully! Welcome to Tender AI.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash(result["error"], "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        result = login_user(email, password)

        if result["success"]:
            session["user_id"] = result["user"]["id"]
            session["user_email"] = email
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash(result["error"], "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))


# ── Protected pages ───────────────────────────────────────────
@app.route("/dashboard")
def dashboard():
    redir = require_login()
    if redir: return redir

    stats = get_dashboard_stats(session["user_id"])
    return render_template("dashboard.html",
                           email=session.get("user_email"),
                           **stats)


@app.route("/analyze", methods=["GET", "POST"])
def analyze():
    redir = require_login()
    if redir: return redir

    user_id = session["user_id"]
    profile = get_company_profile(user_id)

    # ── Step 1: PDF uploaded ──────────────────────────────────
    if request.method == "POST" and request.form.get("step") == "upload":
        if "pdf_file" not in request.files:
            flash("Please upload a PDF file.", "error")
            return render_template("analyze.html", profile=profile)

        pdf_file = request.files["pdf_file"]
        if pdf_file.filename == "":
            flash("No file selected.", "error")
            return render_template("analyze.html", profile=profile)

        # Save PDF to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_file.save(tmp.name)
            tmp_pdf_path = tmp.name

        pages = extract_text_from_pdf(tmp_pdf_path)

        if not pages:
            flash("Could not extract text from PDF.", "error")
            os.unlink(tmp_pdf_path)
            return render_template("analyze.html", profile=profile)

        pdf_text_with_pages = format_pages_for_prompt(pages)

        # ── Save to temp JSON file instead of session ─────────
        data_to_store = {
            "pdf_text": pdf_text_with_pages,
            "pdf_pages": [
                {
                    "page": p["page"],
                    "full_text": p["full_text"],
                    "lines": p["lines"]
                }
                for p in pages
            ]
        }

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".json",
            mode="w",
            encoding="utf-8"
        ) as data_file:
            json.dump(data_to_store, data_file)
            data_file_path = data_file.name

        # Only store the file PATH in session (tiny)
        session["data_file"] = data_file_path
        os.unlink(tmp_pdf_path)

        analysis_profile = dict(profile) if profile else {}
        if request.form.get("override_domain"):
            analysis_profile["domain"] = request.form.get("override_domain")
        if request.form.get("override_turnover"):
            analysis_profile["turnover"] = request.form.get("override_turnover")
        session["analysis_profile"] = analysis_profile

        # First AI call
        q_result = extract_questions(pdf_text_with_pages, analysis_profile)
        if not q_result["success"]:
            flash(f"Error reading tender: {q_result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        return render_template("analyze.html",
                               profile=profile,
                               show_questions=True,
                               questions_data=q_result["data"])

    # ── Step 2: Answers submitted ─────────────────────────────
    if request.method == "POST" and request.form.get("step") == "answers":
        data_file = session.get("data_file")
        analysis_profile = session.get("analysis_profile", {})

        if not data_file or not os.path.exists(data_file):
            flash("Session expired. Please upload the PDF again.", "error")
            return render_template("analyze.html", profile=profile)

        # Load PDF data from temp file
        with open(data_file, "r", encoding="utf-8") as f:
            stored = json.load(f)

        pdf_text = stored.get("pdf_text", "")
        pdf_pages = stored.get("pdf_pages", [])

        # Collect answers
        answers = {}
        for key, value in request.form.items():
            if key.startswith("answer_"):
                question_text = key.replace("answer_", "").replace("_", " ")
                answers[question_text] = value

        # Second AI call with verified citations
        result = analyze_tender(pdf_text, analysis_profile, answers, pages=pdf_pages)

        # Clean up temp file — log if it fails so it doesn't go silently missing
        try:
            os.unlink(data_file)
        except OSError as e:
            print(f"[app] Warning: could not delete temp file {data_file}: {e}")

        session.pop("data_file", None)
        session.pop("analysis_profile", None)

        if not result["success"]:
            flash(f"Analysis failed: {result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        save_tender_analysis(user_id, result["data"])

        return render_template("analyze.html",
                               profile=profile,
                               result=result["data"])

    return render_template("analyze.html", profile=profile)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    redir = require_login()
    if redir: return redir

    user_id = session["user_id"]

    if request.method == "POST":
        profile_data = {
            "company_name": request.form.get("company_name", ""),
            "registration_number": request.form.get("registration_number", ""),
            "pan_number": request.form.get("pan_number", ""),
            "turnover": request.form.get("turnover", 0),
            "experience": request.form.get("experience", 0),
            "domain": request.form.get("domain", ""),
            "sub_domains": request.form.get("sub_domains", "").split(","),
            "employee_count": request.form.get("employee_count", 0),
            "certifications": request.form.get("certifications", ""),
            "address": request.form.get("address", ""),
            "phone": request.form.get("phone", ""),
            "company_email": request.form.get("company_email", ""),
        }
        result = save_company_profile(user_id, profile_data)
        if result["success"]:
            flash("Profile updated successfully!", "success")
        else:
            flash("Error updating profile.", "error")

    company = get_company_profile(user_id)
    return render_template("profile.html", profile=company)


@app.route("/history")
def history():
    redir = require_login()
    if redir: return redir

    records = get_tender_history(session["user_id"])
    return render_template("history.html", history=records)


# ── Health check (for UptimeRobot) ───────────────────────────
@app.route("/ping")
def ping():
    return "OK", 200


# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)