import datetime

import polars as pl

from app.engine.infer import infer_types


def infer(**columns):
    return infer_types(pl.DataFrame({k: pl.Series(k, v, dtype=pl.String) for k, v in columns.items()}))


def col(result, name):
    return next(c for c in result.columns if c["name"] == name)


def kinds(result, name):
    return {s["kind"] for s in result.suggestions if s["column"] == name}


# ------------------------------------------------------------ lossless conversions
def test_clean_integers_and_floats():
    r = infer(i=["1", "-2", "+3", "4"], f=["1.5", "2", ".5", "1e3"])
    assert r.df["i"].to_list() == [1, -2, 3, 4]
    assert r.df["f"].to_list() == [1.5, 2.0, 0.5, 1000.0]
    assert col(r, "i")["dtype"] == "integer" and col(r, "f")["dtype"] == "float"


def test_values_with_surrounding_spaces_still_convert():
    assert infer(n=[" 1", "2 "]).df["n"].to_list() == [1, 2]


def test_leading_zeros_stay_text_with_warning():
    r = infer(zip=["00123", "04567", "12345"])
    assert r.df["zip"].dtype == pl.String
    assert r.df["zip"][0] == "00123"
    assert any("leading zeros" in w for w in r.warnings)
    assert r.suggestions == []


def test_very_long_digit_strings_stay_text():
    r = infer(card=["1234567890123456789012", "1"])
    assert r.df["card"].dtype == pl.String


def test_true_false_becomes_boolean():
    assert infer(b=["true", "False", "TRUE"]).df["b"].to_list() == [True, False, True]


def test_iso_dates_and_excel_style_midnight_datetimes_become_dates():
    r = infer(d=["2024-01-05", "2024-02-29"], x=["2024-01-05 00:00:00", "2024-03-01 00:00:00"])
    assert r.df["d"].dtype == pl.Date and r.df["x"].dtype == pl.Date
    assert r.df["x"][0] == datetime.date(2024, 1, 5)


def test_real_datetimes_keep_time():
    r = infer(t=["2024-01-05 10:30:00", "2024-01-06T11:00:00"])
    # mixed separators: first format that parses ALL values wins, otherwise text
    assert r.df["t"].dtype in (pl.String, pl.Datetime("us"))
    r2 = infer(t=["2024-01-05 10:30:00", "2024-01-06 11:00:00"])
    assert isinstance(r2.df["t"].dtype, pl.Datetime)


def test_invalid_calendar_date_stays_text():
    assert infer(d=["2024-01-05", "2024-13-40"]).df["d"].dtype == pl.String


# ---------------------------------------------------------------- missing markers
def test_standard_markers_become_missing_and_are_counted():
    r = infer(a=["1", "NA", "N/A", "null", "  ", "2", "#N/A"])
    assert r.df["a"].to_list() == [1, None, None, None, None, 2, None]
    assert col(r, "a")["missing_markers"] == {"NA": 1, "N/A": 1, "null": 1, "(blank)": 1, "#N/A": 1}


def test_question_marks_are_not_silently_removed():
    r = infer(age=["25", "?", "31", "40", "38"])
    assert r.df["age"].dtype == pl.String  # NOT converted automatically
    assert r.df["age"][1] == "?"


# ---------------------------------------------------------------------- suggestions
def test_mostly_numeric_suggests_conversion_with_evidence():
    r = infer(age=["25", "?", "31", "-", "40", "38", "29", "33", "41", "50"])
    s = next(s for s in r.suggestions if s["kind"] == "convert_to_number")
    assert s["operation"] == {
        "type": "convert_type",
        "params": {"column": "age", "to": "integer", "treat_as_missing": ["?", "-"]},
    }
    assert s["evidence"]["convertible"] == 8 and s["evidence"]["failing"] == 2


def test_placeholders_lower_the_threshold_but_words_do_not():
    assert "convert_to_number" in kinds(infer(s=["1", "2", "3", "?", "-"]), "s")  # 60%, only symbols
    assert "convert_to_number" not in kinds(infer(s=["1", "2", "3", "unknown", "n.a"]), "s")  # 60%, words


def test_float_target_when_decimals_present():
    r = infer(p=["1.5", "2", "?", "3.25", "4"])
    s = next(s for s in r.suggestions if s["kind"] == "convert_to_number")
    assert s["operation"]["params"]["to"] == "float"


def test_free_text_gets_no_number_suggestion():
    assert kinds(infer(city=["Mumbai", "Pune", "Delhi"]), "city") == set()


def test_currency_and_thousands_suggest_formatted_number():
    r = infer(price=["₹1,20,000", "₹95,000", "₹500"])
    s = next(s for s in r.suggestions if s["kind"] == "parse_formatted_number")
    assert {"before": "₹1,20,000", "after": "120000"} in s["evidence"]["examples"]


def test_day_first_dates_detected():
    s = next(s for s in infer(d=["25/04/2024", "03/05/2024"]).suggestions if s["kind"] == "convert_to_date")
    assert s["operation"]["params"]["format_options"] == ["%d/%m/%Y"]


def test_month_first_dates_detected():
    s = next(s for s in infer(d=["04/25/2024", "05/03/2024"]).suggestions if s["kind"] == "convert_to_date")
    assert s["operation"]["params"]["format_options"] == ["%m/%d/%Y"]


def test_ambiguous_dates_offer_both_orders():
    s = next(s for s in infer(d=["01/02/2024", "03/04/2024"]).suggestions if s["kind"] == "convert_to_date")
    assert len(s["operation"]["params"]["format_options"]) == 2


def test_yes_no_suggests_boolean():
    assert "convert_to_boolean" in kinds(infer(active=["Yes", "no", "YES"]), "active")


def test_padded_text_suggests_trim():
    r = infer(name=[" Asha", "Ravi ", "Meera"])
    s = next(s for s in r.suggestions if s["kind"] == "trim_whitespace")
    assert s["evidence"]["affected"] == 2


def test_all_missing_column_is_reported():
    r = infer(empty=["NA", None, ""])
    assert col(r, "empty")["null_count"] == 3
    assert any("no values" in w for w in r.warnings)


def test_messy_customer_file_end_to_end():
    r = infer(
        zip=["00123", "04567"],
        age=["25", "?"],
        salary=["50000", "-"],
        joined=["2024-01-05", "2024-02-10"],
    )
    assert r.df["zip"].dtype == pl.String
    assert r.df["joined"].dtype == pl.Date
    assert "convert_to_number" in kinds(r, "age")
    assert "convert_to_number" in kinds(r, "salary")
