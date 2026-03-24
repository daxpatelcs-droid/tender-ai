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
from analyzer import extract_text_from_pdf, analyze_tender

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
            user_id = result["user"].id

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
            session["user_id"] = result["user"].id
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

    if request.method == "POST":
        # Check if PDF was uploaded
        if "pdf_file" not in request.files:
            flash("Please upload a PDF file.", "error")
            return render_template("analyze.html", profile=profile)

        pdf_file = request.files["pdf_file"]
        if pdf_file.filename == "":
            flash("No file selected.", "error")
            return render_template("analyze.html", profile=profile)

        # Save to temp file and extract text
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_file.save(tmp.name)
            pdf_text = extract_text_from_pdf(tmp.name)

        if not pdf_text:
            flash("Could not extract text from PDF. Make sure it is not scanned/image-only.", "error")
            return render_template("analyze.html", profile=profile)

        # Use override profile if provided
        analysis_profile = profile.copy() if profile else {}
        if request.form.get("override_domain"):
            analysis_profile["domain"] = request.form.get("override_domain")
        if request.form.get("override_turnover"):
            analysis_profile["turnover"] = request.form.get("override_turnover")

        # Run AI analysis
        result = analyze_tender(pdf_text, analysis_profile)

        if not result["success"]:
            flash(f"Analysis failed: {result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        # Save to history
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
    app.run(debug=True)