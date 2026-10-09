"""
Project-specific mapping — this is the ONLY file you should need to edit
when reusing this template for a new migration project.

Custom field labels always need an explicit entry here (no reliable way to
auto-derive a field name from a column header). Question labels usually
DON'T — migrate.py already strips a leading "1.1 " style number prefix from
a column header and uses the rest as the question text, which is enough for
intake sheets whose headers already match the live questionnaire wording.
Only add a question here when that auto-derived text is wrong (typo,
reworded question, header with no number prefix to strip, etc).

Both maps are validated at the start of every run (see migrate.py) — any
Custom Fields column using a field_label that isn't listed here will be
reported and skipped, not silently dropped. Questions are not validated the
same way since most need no entry at all.
"""

# question_label (a question COLUMN HEADER — on the Use Cases sheet itself
# for single-sheet intake files, or on the "Questionnaire Evidence" tab for
# the older multi-tab template) -> the EXACT question text as it appears in
# the live Credo AI questionnaire. Matching is exact, case-sensitive, on the
# full question text — copy it verbatim from the questionnaire in the Credo
# AI UI or via GET /questionnaires/{key}.
QUESTION_TEXT_MAP: dict[str, str] = {
    # "1.1 Some header that doesn't match after number-stripping": "Exact question text from the live questionnaire",
}

# field_label (a COLUMN HEADER in the "Custom Fields" tab, which is wide
# format: one row per use case, one column per field) -> the EXACT custom
# field name for this tenant (case-insensitive match, but keep it exact for
# clarity).
CUSTOM_FIELD_NAME_MAP: dict[str, str] = {
    "business_function": "Business Function",
    "cost_center": "Cost Center",
    # "your_label_here": "Exact Custom Field Name in Credo AI",
}
