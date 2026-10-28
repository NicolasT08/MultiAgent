<!-- Used by llm/client.py when an output is not valid JSON for its contract (one call, counts for b_max). -->
SYSTEM:
You fix malformed JSON. Reply with a single JSON object and nothing else: no prose, no code fences.

USER:
The following output was supposed to be a JSON object matching this JSON Schema:

{{schema}}

Problem found: {{error}}

Output:
<<<
{{output}}
>>>

Return the corrected json object. Keep the content of every field; only fix the format.
If a required field is missing and cannot be recovered from the output, use an empty string.
