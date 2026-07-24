#!/usr/bin/env python3
"""GBNF grammar definitions for the local pipeline's constrained decoding.

These llama.cpp GBNF grammars are bound onto the sampler at inference time,
making a malformed output shape IMPOSSIBLE. When a lane calls for structured
output (list, email, JSON), the grammar forces the model to stay inside the
required skeleton.

Grammars defined here:
  • list        — bullet or numbered list (one item per line)
  • json_object — valid JSON object with string/number/bool/null/array values
  • email       — structured email: optional Subject, greeting, body, sign-off

Grammar function:
  • grammar_for(lane) → GBNF string (or None for free-form lanes like prompt)

The grammars are raw strings suitable for passing directly to llama.cpp's
`--grammar` flag or LlamaCliBackend.generate(grammar=...).

This module has zero dependencies — it is import-safe everywhere.
"""

# ---------------------------------------------------------------------------
# List grammar — bullet or numbered items, one per line.
# ---------------------------------------------------------------------------
# Forces each line to start with a bullet marker: "-", "*", or a number
# followed by "." (e.g. "1.", "42.", "100.").  After the marker the rest of
# the line is free-form text (any character except newline).  At least one
# item is required — an empty list is NOT valid under this grammar.

GBNF_LIST = r'''
root ::= list
list ::= item ("\n" item)*
item ::= bullet " " [^\n]+
bullet ::= "-" | "*" | [0-9]+ "."
'''

# ---------------------------------------------------------------------------
# JSON object grammar — a minimal but valid JSON object.
# ---------------------------------------------------------------------------
# Supports strings, numbers, booleans, null, nested objects, and arrays.
# Whitespace is allowed everywhere per JSON spec.  This is the same grammar
# used by the cloud lanes for structured extraction presets.

GBNF_JSON_OBJECT = r'''
root    ::= object
object  ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair    ::= string ws ":" ws value
value   ::= string | number | object | array | "true" | "false" | "null"
array   ::= "[" ws (value (ws "," ws value)*)? ws "]"
string  ::= "\"" ([^"\\] | "\\" .)* "\""
number  ::= "-"? [0-9]+ ("." [0-9]+)?
ws      ::= [ \t\n]*
'''

# ---------------------------------------------------------------------------
# Email grammar — structured email skeleton.
# ---------------------------------------------------------------------------
# Optional Subject line, a greeting ("Hi", "Hello", or "Dear"), a body of one
# or more lines, and a sign-off ("Best regards,", "Kind regards,", or "Thanks,")
# followed by a name line.

GBNF_EMAIL = r'''
root     ::= subject? greeting "\n\n" body "\n\n" signoff
subject  ::= "Subject: " line "\n\n"
greeting ::= ("Hi" | "Hello" | "Dear") " " line ","
body     ::= (line "\n"?)+
signoff  ::= ("Best regards," | "Kind regards," | "Thanks,") "\n" line
line     ::= [^\n]+
'''

# ---------------------------------------------------------------------------
# Grammar lookup
# ---------------------------------------------------------------------------

# Map lane names to their GBNF grammar strings.
_GRAMMAR_MAP = {
    "list":        GBNF_LIST,
    "json_object": GBNF_JSON_OBJECT,
    "email":       GBNF_EMAIL,
}


def grammar_for(lane):
    """Return the GBNF grammar string for `lane`, or None for free-form lanes.

    Recognised lanes:
      • "list"        → bullet/numbered list
      • "json_object" → valid JSON object
      • "email"       → structured email

    All other lanes (prompt, reply, text, foreign) return None — they produce
    prose, not a fixed shape, so no grammar constraint is applied.
    """
    return _GRAMMAR_MAP.get(lane)


def list_grammars():
    """Return the list of lane names that have grammar definitions."""
    return sorted(_GRAMMAR_MAP.keys())
