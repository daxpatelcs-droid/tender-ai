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

def analyze_tender(pdf_text, company_profile):
    client = get_groq_client()

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
- Primary Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {', '.join(company_profile.get('sub_domains', []) or [])}
- Annual Turnover: Rs {company_profile.get('turnover', 0)} Lakhs
- Years of Experience: {company_profile.get('experience', 0)} years
- Employee Count: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}

TENDER DOCUMENT TEXT:
{pdf_text[:8000]}

INSTRUCTIONS:
Analyze this tender document thoroughly and return ONLY a valid JSON object.
No explanation, no markdown, no extra text — just raw JSON.

Return exactly this structure:

{{
  "project_name": "full project name",
  "project_value": numeric value in lakhs,
  "location": "project location",
  "deadline": "submission deadline",
  "tender_type": "one of: L1, QCBS, REVERSE_AUCTION, DIRECT, GEM",
  "tender_type_reason": "brief reason why you identified this tender type",
  "qcbs_ratio": "only if QCBS - e.g. 70:30 or 80:20, else null",

  "eligibility_criteria": [
    {{
      "criterion": "criterion name e.g. Annual Turnover",
      "required": "what tender requires e.g. Rs 50 Lakhs",
      "company_has": "what company has e.g. Rs 80 Lakhs",
      "status": "PASS or FAIL or CHECK",
      "note": "brief explanation"
    }}
  ],

  "overall_eligibility": "ELIGIBLE or PARTIALLY_ELIGIBLE or NOT_ELIGIBLE",
  "eligibility_score": integer 0-100,
  "eligibility_summary": "2-3 sentence plain English explanation of eligibility status",

  "bid_recommendation": "BID or CONDITIONAL_BID or DO_NOT_BID",
  "bid_recommendation_reason": "clear reason for recommendation",

  "t_score_estimate": integer 0-100 or null if not QCBS,
  "t1_gap": "what company needs to improve to reach T1, or null if not QCBS",
  "l1_strategy": "pricing advice for L1 tenders or null if not L1",

  "financial_requirements": {{
    "emd_amount": "EMD amount if mentioned else Unknown",
    "performance_guarantee": "percentage or amount if mentioned else Unknown",
    "payment_terms": "brief payment terms if mentioned else Unknown",
    "working_capital_needed": "estimated working capital needed"
  }},

  "key_dates": [
    {{
      "event": "event name e.g. Pre-bid Meeting",
      "date": "date or Unknown"
    }}
  ],

  "documents_required": [
    "document 1",
    "document 2"
  ],

  "gem_specific": {{
    "gem_bid_number": "GeM bid number if GeM tender else null",
    "oem_required": true or false,
    "msme_preference": true or false,
    "startup_preference": true or false
  }},

  "red_flags": [
    "any suspicious or unfair clauses found, empty array if none"
  ],

  "recommendations": [
    "actionable recommendation 1",
    "actionable recommendation 2",
    "actionable recommendation 3"
  ],

  "summary": "3-4 sentence plain English summary of the tender and company fit"
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

        # Strip markdown code fences if present
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        return {"success": True, "data": result}

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw response: {raw}")
        return {"success": False, "error": "AI returned invalid response. Please try again."}
    except Exception as e:
        print(f"Groq API error: {e}")
        return {"success": False, "error": str(e)}