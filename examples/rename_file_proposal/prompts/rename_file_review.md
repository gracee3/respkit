You are an accuracy reviewer.

Original context:
- old_filename: {old_filename}
- source_token: {source_token}
- candidate_date: {candidate_date}
- candidate_time: {candidate_time}

First pass output:
{first_output}

Document text:
{text}

Return exactly one JSON object and nothing else.
No markdown fences, prose, comments, preamble, or trailing text.

The JSON must contain:
- decision: "pass", "fail", or "uncertain"
- notes: short review notes
- recommended_adjustments: string containing optional adjustment recommendation

{{
  "decision": "pass",
  "notes": "Review notes.",
  "recommended_adjustments": "Optional adjustment text."
}}
