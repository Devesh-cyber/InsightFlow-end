"""
The analysis report: one data model (builder.py), two renderers.

    builder.build_report(...) -> dict   plain JSON-safe data, no formatting decisions left
    html.render_html(report)  -> str    print-ready A4 page in the Sage theme (Save as PDF from the browser)
    docx.render_docx(report)  -> bytes  editable Word file for coursework

Both renderers read the same dict, so the two formats can never disagree.
"""
