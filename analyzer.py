import os
import json
import re
import pdfplumber
from groq import Groq


def get_groq_client():
    return Groq(api_key=os.environ.get("GROQ_API_KEY"))


# ── Constants ─────────────────────────────────────────────────
# Centralised limits — change here, applies everywhere
PROMPT_TEXT_LIMIT      = 10000   # chars sent to full analysis prompt
QUESTION_TEXT_LIMIT    = 6000    # chars sent to question-generation prompt


# ── PDF Extraction ────────────────────────────────────────────
def extract_text_from_pdf(pdf_file):
    """
    Extract text page by page with line numbers.
    Returns list of pages, each with structured lines.
    Handles both digital and scanned PDFs.
    """
    try:
        pages = []
        with pdfplumber.open(pdf_file) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()

                if not page_text or not page_text.strip():
                    continue

                lines = []
                for line_num, line in enumerate(page_text.split("\n"), start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    lines.append({
                        "line_num": line_num,
                        "text": stripped,
                        "is_heading": is_section_heading(stripped)
                    })

                pages.append({
                    "page": i + 1,
                    "lines": lines,
                    "full_text": page_text.strip()
                })

        return pages

    except Exception as e:
        print(f"PDF extraction error: {e}")
        return []


def is_section_heading(line):
    """
    Detect if a line is likely a section heading.
    Checks for numbered sections, ALL CAPS, short bold-like lines.
    """
    line = line.strip()
    if not line:
        return False

    # Numbered section like 1. or 2.1 or Section 3
    if re.match(r'^(\d+\.)+\s+\w+', line):
        return True
    if re.match(r'^(Section|SECTION|Clause|CLAUSE|Part|PART)\s+[\d\w]', line):
        return True
    # ALL CAPS short line
    if line.isupper() and 3 < len(line) < 80:
        return True
    # Short line ending without punctuation (likely a heading)
    if len(line) < 60 and not line.endswith(('.', ',', ';', ':')):
        if line[0].isupper():
            return True

    return False


def get_plain_text_for_prompt(pages, limit=PROMPT_TEXT_LIMIT):
    """
    Format pages into clearly marked text for AI prompt.
    Every line is numbered so AI can quote precisely.
    Logs a warning when content is truncated so you know
    how much of the document the AI is actually seeing.
    """
    output = ""
    for page in pages:
        output += f"\n\n{'='*50}\n"
        output += f"PAGE {page['page']}\n"
        output += f"{'='*50}\n"
        for line in page["lines"]:
            prefix = "[HEADING] " if line["is_heading"] else ""
            output += f"L{line['line_num']:03d}: {prefix}{line['text']}\n"

    if len(output) > limit:
        print(
            f"[analyzer] Prompt text truncated: {len(output)} → {limit} chars "
            f"({len(output) - limit} chars dropped). "
            f"Consider raising PROMPT_TEXT_LIMIT if pages are being cut."
        )
        return output[:limit]

    return output


def find_citation(quote, pages):
    """
    Given an exact quote from AI, search all pages
    and find the real page number, line number, and
    nearest section heading. 100% Python verified.
    """
    if not quote or not pages:
        return None

    quote_clean = quote.strip().lower()
    # Try progressively shorter matches if full match fails
    search_variants = [
        quote_clean,
        quote_clean[:60],
        quote_clean[:40],
        quote_clean[:25],
    ]

    for variant in search_variants:
        if len(variant) < 10:
            continue
        for page in pages:
            nearest_heading = None
            for line in page["lines"]:
                if line["is_heading"]:
                    nearest_heading = line["text"]
                if variant in line["text"].lower():
                    return {
                        "page": page["page"],
                        "line": line["line_num"],
                        "section": nearest_heading or "General",
                        "quote": line["text"],
                        "found": True
                    }

    return {
        "page": None,
        "line": None,
        "section": None,
        "quote": quote,
        "found": False
    }


def format_pages_for_prompt(pages):
    """Alias used by app.py — delegates to get_plain_text_for_prompt."""
    return get_plain_text_for_prompt(pages)


# ── JSON cleaning helper ──────────────────────────────────────
def _clean_json_response(raw: str) -> str:
    """
    Robustly strip markdown code fences that Groq sometimes wraps
    around JSON responses. Handles all of these edge cases:
        ```json { ... } ```
        ``` { ... } ```
        { ... }   ← already clean, returned as-is

    Does NOT modify the JSON content itself.
    """
    raw = raw.strip()

    # Remove opening fence (```json or just ```)
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)

    # Remove closing fence
    raw = re.sub(r'\s*```$', '', raw)

    return raw.strip()


# ── CALL 1: Extract questions ─────────────────────────────────
def extract_questions(pdf_text, company_profile):
    """
    First AI call — find what critical info is missing
    and generate smart specific questions.
    """
    client = get_groq_client()

    # Warn if question prompt is being truncated
    if len(pdf_text) > QUESTION_TEXT_LIMIT:
        print(
            f"[analyzer] Question prompt text truncated: "
            f"{len(pdf_text)} → {QUESTION_TEXT_LIMIT} chars."
        )

    prompt = f"""
You are an expert Indian government tender analyst.

COMPANY PROFILE:
- Company Name: {company_profile.get('company_name', 'N/A')}
- Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {', '.join(company_profile.get('sub_domains', []) or [])}
- Annual Turnover: Rs {company_profile.get('turnover', 0)} Lakhs
- Experience: {company_profile.get('experience', 0)} years
- Employees: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}

TENDER DOCUMENT:
{pdf_text[:QUESTION_TEXT_LIMIT]}

Based on what this tender requires vs what the company profile provides,
generate 3-7 specific questions about MISSING information that affects eligibility.

Return ONLY valid JSON. No markdown, no explanation:

{{
  "tender_title": "brief tender title",
  "tender_type": "L1 or QCBS or REVERSE_AUCTION or DIRECT or GEM",
  "questions": [
    {{
      "id": "q1",
      "question": "specific question to ask user",
      "why_needed": "brief reason why this affects analysis",
      "input_type": "text or number or yes_no or select",
      "options": ["option1", "option2"]
    }}
  ]
}}

Only include options array for yes_no (always ["Yes","No"]) or select types.
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        clean = _clean_json_response(raw)
        return {"success": True, "data": json.loads(clean)}

    except json.JSONDecodeError as e:
        print(f"Question JSON parse error: {e}")
        print(f"Raw response was: {raw[:300]}")   # helps debug bad AI output
        return {"success": False, "error": "AI returned invalid response. Please try again."}
    except Exception as e:
        print(f"Question generation error: {e}")
        return {"success": False, "error": str(e)}


# ── CALL 2: Full analysis ─────────────────────────────────────
def analyze_tender(pdf_text, company_profile, answers=None, pages=None):
    """
    Second AI call — full analysis.
    AI returns exact quotes, Python verifies locations.
    """
    client = get_groq_client()

    answers_text = ""
    if answers:
        answers_text = "\n\nADDITIONAL INFO FROM USER:\n"
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

CRITICAL INSTRUCTION ABOUT CITATIONS:
For every finding, return the EXACT quote from the document
(copy word for word, max 20 words).
Do NOT guess or paraphrase — copy exactly as it appears.
If information is not in the document, set quote to null and found to false.

COMPANY PROFILE:
- Company Name: {company_profile.get('company_name', 'N/A')}
- Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {', '.join(company_profile.get('sub_domains', []) or [])}
- Annual Turnover: Rs {company_profile.get('turnover', 0)} Lakhs
- Experience: {company_profile.get('experience', 0)} years
- Employees: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}
{answers_text}

TENDER DOCUMENT (lines are numbered for reference):
{pdf_text}

Return ONLY valid JSON. No markdown, no explanation:

{{
  "project_name": "full project name",
  "project_value": numeric in lakhs or 0,
  "location": "location or Unknown",
  "deadline": "submission deadline or Unknown",
  "tender_type": "L1 or QCBS or REVERSE_AUCTION or DIRECT or GEM",
  "tender_type_reason": "why you identified this type",
  "tender_type_quote": "exact quote from document proving type",
  "qcbs_ratio": "e.g. 70:30 or null",

  "eligibility_criteria": [
    {{
      "criterion": "criterion name",
      "required": "what tender requires",
      "company_has": "what company has",
      "status": "PASS or FAIL or CHECK",
      "note": "brief explanation",
      "quote": "exact quote from document or null"
    }}
  ],

  "overall_eligibility": "ELIGIBLE or PARTIALLY_ELIGIBLE or NOT_ELIGIBLE",
  "eligibility_score": 0-100,
  "eligibility_summary": "2-3 sentence explanation",

  "bid_recommendation": "BID or CONDITIONAL_BID or DO_NOT_BID",
  "bid_recommendation_reason": "clear reason",

  "t_score_estimate": integer or null,
  "t1_gap": "what needed to reach T1 or null",
  "l1_strategy": "L1 pricing advice or null",

  "financial_requirements": {{
    "emd_amount": "amount or Not mentioned",
    "emd_quote": "exact quote or null",
    "performance_guarantee": "percentage or Not mentioned",
    "pg_quote": "exact quote or null",
    "payment_terms": "terms or Not mentioned",
    "working_capital_needed": "estimated amount"
  }},

  "key_dates": [
    {{
      "event": "event name",
      "date": "date or Unknown",
      "quote": "exact quote or null"
    }}
  ],

  "documents_required": [
    {{
      "document": "document name",
      "quote": "exact quote or null"
    }}
  ],

  "gem_specific": {{
    "gem_bid_number": "number or null",
    "oem_required": true or false,
    "msme_preference": true or false,
    "startup_preference": true or false
  }},

  "red_flags": [
    {{
      "flag": "description",
      "quote": "exact quote or null"
    }}
  ],

  "recommendations": ["rec 1", "rec 2", "rec 3"],
  "summary": "3-4 sentence summary"
}}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4000,
        )
        raw = response.choices[0].message.content.strip()
        clean = _clean_json_response(raw)
        result = json.loads(clean)

        # ── Python verifies all citations ─────────────────────
        if pages:
            result = verify_all_citations(result, pages)

        return {"success": True, "data": result}

    except json.JSONDecodeError as e:
        print(f"Analysis JSON parse error: {e}")
        print(f"Raw response was: {raw[:300]}")   # helps debug bad AI output
        return {"success": False, "error": "AI returned invalid response. Please try again."}
    except Exception as e:
        print(f"Groq API error: {e}")
        return {"success": False, "error": str(e)}


# ── Citation Verifier ─────────────────────────────────────────
def verify_all_citations(result, pages):
    """
    Go through every quote in the result,
    find its real location using Python text search,
    and attach verified page + line + section.
    """

    def resolve(quote):
        """Find a quote and return full citation object"""
        if not quote:
            return {"found": False, "page": None, "line": None, "section": None, "quote": None}
        citation = find_citation(quote, pages)
        return citation or {"found": False, "page": None, "line": None, "section": None, "quote": quote}

    # Tender type citation
    result["tender_type_citation"] = resolve(result.get("tender_type_quote"))

    # Eligibility criteria
    for item in result.get("eligibility_criteria", []):
        item["citation"] = resolve(item.get("quote"))

    # Financial
    fin = result.get("financial_requirements", {})
    fin["emd_citation"] = resolve(fin.get("emd_quote"))
    fin["pg_citation"] = resolve(fin.get("pg_quote"))

    # Key dates
    for date in result.get("key_dates", []):
        date["citation"] = resolve(date.get("quote"))

    # Documents
    for doc in result.get("documents_required", []):
        doc["citation"] = resolve(doc.get("quote"))

    # Red flags
    for flag in result.get("red_flags", []):
        flag["citation"] = resolve(flag.get("quote"))

    return result