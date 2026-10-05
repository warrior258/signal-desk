import json
import re
import sys
from pathlib import Path
from google import genai
from google.genai import errors

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import GEMINI_API_KEY, GEMINI_MODELS

_CLIENT = None


def is_available() -> bool:
    """True if a Gemini key is configured. Lets callers skip analysis cleanly."""
    return bool(GEMINI_API_KEY)


def get_client():
    global _CLIENT
    if _CLIENT is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in .env")
        _CLIENT = genai.Client(api_key=GEMINI_API_KEY)
    return _CLIENT

ANALYSIS_PROMPT = """You are a senior financial policy analyst specialized in the Indian economy and NSE/BSE stock markets.

Analyze the Indian government / regulatory policy document below and determine its commercial and equity market impact.

Return ONLY a valid JSON object with EXACTLY this structure, and no additional commentary or explanation outside the JSON:
{
  "summary": "2-3 sentence clear, objective summary of what was approved, proposed, or ordered",
  "document_stage": "draft | consultation | final_notification | bill_introduced | bill_passed | announcement | other",
  "effective_date": "YYYY-MM-DD or null",
  "issuing_body": "Name of Ministry, Department, or Regulator (e.g. MeitY, MoRTH, DGFT, BIS, MoPNG, RBI, SEBI, Cabinet)",
  "sectors_positive": ["list of sectors that benefit"],
  "sectors_negative": ["list of sectors harmed or burdened"],
  "companies": [
    {
      "name": "Full official company name if listed in India",
      "direction": "positive | negative | unclear",
      "relevance": "primary | secondary | indirect",
      "revenue_mechanism": "Specific mechanism by which revenue or operating cost changes (e.g. direct subsidy grant, mandatory domestic purchase quota, import tariff protection, new compliance fee)",
      "reason": "Concrete explanation of impact on this specific company"
    }
  ],
  "impact_score": 1-10,
  "impact_reason": "Brief explanation for the assigned impact score",
  "time_horizon": "intraday | days | weeks | months",
  "confidence": "low | medium | high"
}

Strict Extraction & Ranking Guidelines:
1. ONLY name specific Indian companies if you have reasonable basis to believe they are publicly listed or key players in the domain.
2. RELEVANCE:
   - "primary": Directly targeted or directly eligible for the policy benefit/penalty (e.g. sugar mills for ethanol subvention, satellite builders for space FDI).
   - "secondary": Suppliers, component makers, or customers closely tied to primary targets.
   - "indirect": Broader ecosystem players or general conglomerates.
3. SPECIFICITY: Every company MUST have a distinct `revenue_mechanism`. DROP companies whose reason is generic fluff like "will benefit from growth", "general expansion", or "market participant". If no specific listed companies have a direct link, leave `companies` as an empty list [].
4. UNCLEAR DIRECTION: If the policy is a consultation paper, discussion draft, or regulatory overhaul where the final outcome or directional impact on a company cannot be determined (e.g. TRAI interconnection paper), you MUST set `"direction": "unclear"`.
5. IMPACT SCORE:
   - 8-10: Major revenue, margin, or market-access shift for an entire sector or specific firms (e.g. blending mandate, import bans, large capital subvention).
   - 5-7: Moderate regulatory change, consultation with commercial stakes, or targeted tender.
   - 1-4: Routine appointments, MoU with non-commercial scope, procedural amendments.

DOCUMENT TEXT:
<<<TEXT>>>
"""

def analyze_document(title: str, text: str) -> tuple[dict | None, str]:
    """
    Sends document text to Gemini LLM and extracts structured JSON analysis.
    Returns (parsed_dict, raw_json_str).
    """
    if not is_available():
        print("[WARN] GEMINI_API_KEY not set; skipping LLM analysis.")
        return None, ""

    try:
        client = get_client()
    except ValueError as e:
        print(f"[WARN] {e}")
        return None, ""

    combined_text = f"TITLE: {title}\n\nCONTENT:\n{text}"[:15000]
    prompt = ANALYSIS_PROMPT.replace("<<<TEXT>>>", combined_text)

    last_error = None
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            raw_text = response.text or ""

            # Strip markdown code fences if present
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

            parsed = json.loads(cleaned)

            # Normalize companies representation: ensure uniform list with direction, relevance, mechanism
            norm_companies = []
            if "companies" in parsed and isinstance(parsed["companies"], list):
                norm_companies = parsed["companies"]
            else:
                # Fallback for backward compatibility
                for d_key, dir_val in [("companies_positive", "positive"), ("companies_negative", "negative"), ("companies_unclear", "unclear")]:
                    for c in parsed.get(d_key, []):
                        c["direction"] = c.get("direction", dir_val)
                        norm_companies.append(c)

            # Sanitize and ensure required fields
            clean_company_list = []
            for c in norm_companies:
                name = c.get("name", "").strip()
                reason = c.get("reason", "").strip()
                # Drop generic fluff
                if not name or len(name) < 2:
                    continue
                if any(fluff in reason.lower() for fluff in ["will benefit from growth", "general growth", "expansion in sector"]):
                    if not c.get("revenue_mechanism"):
                        continue

                clean_company_list.append({
                    "name": name,
                    "direction": c.get("direction", "positive").lower().strip(),
                    "relevance": c.get("relevance", "primary").lower().strip(),
                    "revenue_mechanism": c.get("revenue_mechanism", "").strip(),
                    "reason": reason,
                })

            parsed["companies"] = clean_company_list
            return parsed, cleaned

        except json.JSONDecodeError as e:
            # Model replied, but not with usable JSON. Worth seeing in the logs.
            last_error = e
            print(f"[WARN] {model_name} returned non-JSON output: {e}")
            continue
        except errors.APIError as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            print(f"[WARN] {model_name} failed: {type(e).__name__}: {e}")
            continue

    print(f"[ERROR] LLM analysis failed across models {GEMINI_MODELS}: {last_error}")
    return None, ""

if __name__ == "__main__":
    test_title = "Cabinet approves amendment in FDI policy for Space Sector"
    test_body = "The Union Cabinet approved up to 100% FDI in space sector for satellite manufacturing, operation, and launch vehicle launch pads."
    if not is_available():
        print("[INFO] GEMINI_API_KEY not set in .env; cannot run a live extraction test.")
        raise SystemExit(0)
    data, raw = analyze_document(test_title, test_body)
    if data:
        print("Analysis succeeded!")
        print(f"Summary: {data.get('summary')}")
        print("Companies:")
        for c in data.get("companies", []):
            print(f" - {c['name']} | Direction: {c['direction']} | Relevance: {c['relevance']} | Mechanism: {c['revenue_mechanism']}")
    else:
        print("Failed to analyze.")
