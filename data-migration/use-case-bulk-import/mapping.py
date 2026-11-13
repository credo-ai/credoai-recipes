"""
Spreadsheet-to-Credo AI name mapping. This is the only file you normally
edit for your own import.

Custom field labels always need an entry: a column header can't be turned
into a custom field name automatically. Question columns usually don't:
migrate.py strips a leading "1.1 " style number from the header and matches
the rest against the questionnaire's question text. Add a question here only
when the header still won't match (a typo or reworded question).

Custom Fields column labels are checked at the start of every run. A label
missing from CUSTOM_FIELD_NAME_MAP is reported and its values are skipped.
"""

# Question column header (on the Use Cases sheet for a single-sheet intake
# file, or on the "Questionnaire Evidence" tab of the multi-tab template) ->
# the exact question text in the Credo AI questionnaire. Copy it from the
# questionnaire in the Credo AI UI or from GET /questionnaires/{key}.
QUESTION_TEXT_MAP: dict[str, str] = {
    # "1.1 Header that doesn't match the question": "Exact question text from the questionnaire",
}

# Custom Fields column label (the "Custom Fields" tab has one row per use
# case and one column per field) -> the custom field's name in Credo AI.
# Matched ignoring case.
CUSTOM_FIELD_NAME_MAP: dict[str, str] = {
    "business_function": "Business Function",
    "cost_center": "Cost Center",
    # "your_label_here": "Custom Field Name in Credo AI",
}
