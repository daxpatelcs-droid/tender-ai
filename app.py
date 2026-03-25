import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
import tempfile

load_dotenv()  # loads .env for local development

from auth import (
    register_user, login_user,
    get_company_profile, save_company_profile,
    save_tender_analysis, get_tender_history,
    get_dashboard_stats
)
from analyzer import extract_text_from_pdf, extract_questions, analyze_tender

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tender-ai-secret-2024")


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

    # ── Step 1: PDF uploaded → extract questions ──────────────
    if request.method == "POST" and request.form.get("step") == "upload":
        if "pdf_file" not in request.files:
            flash("Please upload a PDF file.", "error")
            return render_template("analyze.html", profile=profile)

        pdf_file = request.files["pdf_file"]
        if pdf_file.filename == "":
            flash("No file selected.", "error")
            return render_template("analyze.html", profile=profile)

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_file.save(tmp.name)
            pdf_text = extract_text_from_pdf(tmp.name)
            # Store path for second call
            session["tmp_pdf_path"] = tmp.name

        if not pdf_text:
            flash("Could not extract text from PDF.", "error")
            return render_template("analyze.html", profile=profile)

        # Store pdf text in session for second call
        session["pdf_text"] = pdf_text[:8000]

        # Override profile if provided
        analysis_profile = dict(profile) if profile else {}
        if request.form.get("override_domain"):
            analysis_profile["domain"] = request.form.get("override_domain")
        if request.form.get("override_turnover"):
            analysis_profile["turnover"] = request.form.get("override_turnover")

        session["analysis_profile"] = analysis_profile

        # First AI call — get questions
        q_result = extract_questions(pdf_text, analysis_profile)

        if not q_result["success"]:
            flash(f"Error reading tender: {q_result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        # Return page with questions popup data
        return render_template("analyze.html",
                               profile=profile,
                               show_questions=True,
                               questions_data=q_result["data"])

    # ── Step 2: Answers submitted → final analysis ────────────
    if request.method == "POST" and request.form.get("step") == "answers":
        pdf_text = session.get("pdf_text", "")
        analysis_profile = session.get("analysis_profile", {})

        if not pdf_text:
            flash("Session expired. Please upload the PDF again.", "error")
            return render_template("analyze.html", profile=profile)

        # Collect all answers from form
        answers = {}
        for key, value in request.form.items():
            if key.startswith("answer_"):
                question_text = key.replace("answer_", "").replace("_", " ")
                answers[question_text] = value

        # Second AI call — full analysis with answers
        result = analyze_tender(pdf_text, analysis_profile, answers)

        if not result["success"]:
            flash(f"Analysis failed: {result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        # Save to history
        save_tender_analysis(user_id, result["data"])

        # Clear session data
        session.pop("pdf_text", None)
        session.pop("analysis_profile", None)
        session.pop("tmp_pdf_path", None)

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