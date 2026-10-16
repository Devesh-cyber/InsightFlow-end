"""Report feature: builder text, HTML escaping, DOCX validity and the API end to end."""

import io
import zipfile

import pytest

from app.report.builder import fmt_num, fmt_pct
from tests.conftest import USER_A, USER_B
from tests.helpers import MESSY_CSV

AGE_FIX = {"type": "convert_type", "params": {"column": "Age", "to": "integer", "treat_as_missing": ["?"]}}


@pytest.fixture
def ds(api, auth_header):
    response = api.post(
        "/v1/datasets", params={"filename": "customers.csv"}, content=MESSY_CSV,
        headers={**auth_header(sub=USER_A), "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def report(api, auth_header, ds, user=USER_A, **params):
    return api.get(f"/v1/datasets/{ds['id']}/report", params=params, headers=auth_header(sub=user))


def test_number_formatting():
    assert fmt_num(58200.0) == "58,200"
    assert fmt_num(8843.0812) == "8,843.08"
    assert fmt_num(0.42412) == "0.4241"
    assert fmt_num(None) == "—"
    assert fmt_pct(95.238) == "95.2%"


def test_json_report_before_any_cleaning(api, auth_header, ds):
    r = report(api, auth_header, ds, format="json").json()
    assert r["meta"]["title"] == "Data report: customers" or r["meta"]["title"].startswith("Data report:")
    assert r["meta"]["version"] == 1
    assert r["quality"]["before_version"] is None
    assert any("No cleaning steps" in f for f in r["summary"]["findings"])
    assert len(r["dictionary"]) == 6 and len(r["profiles"]) == 6
    assert r["cleaning"]["count"] == 0
    assert "import pandas as pd" in r["recipe"]


def test_report_after_cleaning_compares_before_and_after(api, auth_header, ds):
    applied = api.post(f"/v1/datasets/{ds['id']}/apply", json={**AGE_FIX, "expected_version": 1}, headers=auth_header(sub=USER_A))
    assert applied.status_code == 200, applied.text
    r = report(api, auth_header, ds, format="json", title="Customer data audit", author="Devesh Kadam").json()
    assert r["meta"]["title"] == "Customer data audit" and r["meta"]["author"] == "Devesh Kadam"
    assert r["quality"]["before_version"] == 1 and r["meta"]["version"] == 2
    score_row = next(c for c in r["quality"]["comparison"] if c["label"] == "Health score")
    assert score_row["before"] != score_row["after"] and score_row["change"] == "better"
    assert r["cleaning"]["count"] == 1 and [s["version"] for s in r["cleaning"]["steps"]] == [1, 2]
    assert any("cleaning step" in f for f in r["summary"]["findings"])
    age = next(p for p in r["profiles"] if p["name"] == "Age")
    assert age["chart"]["kind"] == "histogram"
    assert any("non-empty value" in n for n in age["notes"])  # small-sample caveat


def test_report_for_an_older_version(api, auth_header, ds):
    api.post(f"/v1/datasets/{ds['id']}/apply", json={**AGE_FIX, "expected_version": 1}, headers=auth_header(sub=USER_A))
    r = report(api, auth_header, ds, format="json", version=1).json()
    assert r["meta"]["version"] == 1 and len(r["cleaning"]["steps"]) == 1


def test_html_report_is_escaped_and_script_free(api, auth_header):
    hostile = b"<script>alert(1)</script>,b\n1,x\n2,y\n3,z\n"
    created = api.post("/v1/datasets", params={"filename": "evil.csv"}, content=hostile, headers=auth_header(sub=USER_A)).json()
    response = api.get(f"/v1/datasets/{created['id']}/report", params={"format": "html", "title": "<b>T</b>"},
                       headers=auth_header(sub=USER_A))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" in response.headers["content-disposition"]
    assert "script-src" not in response.headers["content-security-policy"]  # no scripts allowed at all
    body = response.text
    assert "<script" not in body.lower()
    assert "&lt;script&gt;" in body and "&lt;b&gt;T&lt;/b&gt;" in body


def test_html_report_has_every_section(api, auth_header, ds):
    body = report(api, auth_header, ds, format="html").text
    for heading in ["Summary", "The data", "Data quality", "Cleaning log", "Column profiles", "Relationships", "Reproduce it", "Methods"]:
        assert heading in body, heading
    assert "<svg" in body  # charts are inline


def test_docx_report_is_a_valid_word_file(api, auth_header, ds):
    response = report(api, auth_header, ds, format="docx", author="Devesh")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert '.docx"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        xml = z.read("word/document.xml").decode()
    assert "Data quality" in xml and "Devesh" in xml and "Zip Code" in xml


def test_report_requires_ownership_and_valid_format(api, auth_header, ds):
    assert report(api, auth_header, ds, user=USER_B).status_code == 404
    assert report(api, auth_header, ds, format="pdf").status_code == 422
    assert report(api, auth_header, ds, title="x" * 200).status_code == 422


def test_report_time_zone(api, auth_header, ds):
    r = report(api, auth_header, ds, format="json", tz="Asia/Kolkata").json()
    assert r["meta"]["generated_display"].endswith("IST") and r["meta"]["generated_at"].endswith("+05:30")
    fallback = report(api, auth_header, ds, format="json", tz="Mars/Base").json()
    assert fallback["meta"]["generated_display"].endswith("UTC")
    assert report(api, auth_header, ds, format="json", tz="../etc/passwd").status_code == 422


def test_correlations_show_two_decimals():
    from app.report.builder import fmt_r
    assert fmt_r(1.0) == "1.00" and fmt_r(-0.4567) == "-0.46"
