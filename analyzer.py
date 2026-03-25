import os
import json
import pdfplumber
from groq import Groq

def get_groq_client():
    return Groq(api_key=os.environ.get("GROQ_API_KEY"))

def extract_text_from_pdf(pdf_file):
    try:
        text = ""
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip()
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return ""


# ── CALL 1: Analyze PDF and find missing info ─────────────────
def extract_questions(pdf_text, company_profile):
    """
    First AI call — read the tender and figure out
    what critical info is missing from the company profile
    to do a proper analysis
    """
    client = get_groq_client()

    prompt = f"""
You are an expert Indian government tender analyst.

You have been given a tender document and a company profile.
Your job is to:
1. Read the tender carefully
2. Compare it against the company profile
3. Identify what CRITICAL information is missing that would affect eligibility or bid decision
4. Generate smart, specific questions to fill those gaps

COMPANY PROFILE:
- Company Name: {company_profile.get('company_name', 'N/A')}
- Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {', '.join(company_profile.get('sub_domains', []) or [])}
- Annual Turnover: Rs {company_profile.get('turnover', 0)} Lakhs
- Experience: {company_profile.get('experience', 0)} years
- Employees: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}

TENDER DOCUMENT:
{pdf_text[:8000]}

INSTRUCTIONS:
Based on what the tender requires vs what the company profile provides,
generate 3-7 specific questions that would help complete the analysis.

Questions should be:
- Specific to THIS tender (not generic)
- Only about things NOT already in the company profile
- Focused on what actually affects eligibility or bid decision
- Written in simple plain English

Return ONLY a valid JSON object. No markdown, no explanation:

{{
  "tender_title": "brief tender title",
  "tender_type": "L1 or QCBS or REVERSE_AUCTION or DIRECT or GEM",
  "questions": [
    {{
      "id": "q1",
      "question": "the actual question to ask the user",
      "why_needed": "brief reason why this affects the analysis",
      "input_type": "text or number or yes_no or select",
      "options": ["option1", "option2"] 
    }}
  ]
}}

For input_type:
- yes_no → simple yes or no question
- number → requires a numeric answer
- text → requires a text answer
- select → multiple choice (provide options array)

Only include "options" array when input_type is "select" or "yes_no".
For yes_no always set options to ["Yes", "No"].
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )

        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        return {"success": True, "data": result}

    except Exception as e:
        print(f"Question generation error: {e}")
        return {"success": False, "error": str(e)}


# ── CALL 2: Final analysis with all info ──────────────────────
def analyze_tender(pdf_text, company_profile, answers=None):
    """
    Second AI call — do the full analysis using
    tender PDF + company profile + user answers
    """
    client = get_groq_client()

    # Format answers into readable text
    answers_text = ""
    if answers:
        answers_text = "\n\nADDITIONAL COMPANY INFORMATION (from clarification questions):\n"
        for q, a in answers.items():
            answers_text += f"- {q}: {a}\n"

    prompt = f"""
You are an expert Indian government tender analyst with deep knowledge of:
- GeM (Government e-Marketplace) portal tenders
- L1 (Lowest Bidder) based tenders
- QCBS (Quality and Cost Based Selection) tenders
- Reverse Auction tenders
- Direct/Nomination based tenders
- Indian procurement rules (GFR 2017, CVC guidelines)

COMPANY PROFILE:
- Company Name: {company_profile.get('company_name', 'N/A')}
- Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {', '.join(company_profile.get('sub_domains', []) or [])}
- Annual Turnover: Rs {company_profile.get('turnover', 0)} Lakhs
- Experience: {company_profile.get('experience', 0)} years
- Employees: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}
{answers_text}

TENDER DOCUMENT:
{pdf_text[:8000]}

INSTRUCTIONS:
Do a thorough analysis using ALL information provided above.
Return ONLY a valid JSON object. No markdown, no explanation:

{{
  "project_name": "full project name",
  "project_value": numeric value in lakhs,
  "location": "project location",
  "deadline": "submission deadline",
  "tender_type": "L1 or QCBS or REVERSE_AUCTION or DIRECT or GEM",
  "tender_type_reason": "why you identified this tender type",
  "qcbs_ratio": "e.g. 70:30 or null if not QCBS",

  "eligibility_criteria": [
    {{
      "criterion": "criterion name",
      "required": "what tender requires",
      "company_has": "what company has including answers provided",
      "status": "PASS or FAIL or CHECK",
      "note": "brief explanation"
    }}
  ],

  "overall_eligibility": "ELIGIBLE or PARTIALLY_ELIGIBLE or NOT_ELIGIBLE",
  "eligibility_score": integer 0-100,
  "eligibility_summary": "2-3 sentence explanation",

  "bid_recommendation": "BID or CONDITIONAL_BID or DO_NOT_BID",
  "bid_recommendation_reason": "clear reason",

  "t_score_estimate": integer 0-100 or null,
  "t1_gap": "what company needs to reach T1 or null",
  "l1_strategy": "L1 pricing advice or null",

  "financial_requirements": {{
    "emd_amount": "EMD amount or Unknown",
    "performance_guarantee": "percentage or Unknown",
    "payment_terms": "payment terms or Unknown",
    "working_capital_needed": "estimated amount"
  }},

  "key_dates": [
    {{
      "event": "event name",
      "date": "date or Unknown"
    }}
  ],

  "documents_required": ["document 1", "document 2"],

  "gem_specific": {{
    "gem_bid_number": "number or null",
    "oem_required": true or false,
    "msme_preference": true or false,
    "startup_preference": true or false
  }},

  "red_flags": ["suspicious clause 1"],

  "recommendations": [
    "recommendation 1",
    "recommendation 2",
    "recommendation 3"
  ],

  "summary": "3-4 sentence summary of tender and company fit"
}}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3000,
        )

        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        return {"success": True, "data": result}

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return {"success": False, "error": "AI returned invalid response. Please try again."}
    except Exception as e:
        print(f"Groq API error: {e}")
        return {"success": False, "error": str(e)}