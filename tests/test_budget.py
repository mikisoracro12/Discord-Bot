from app.extractor.budget import extract_budget

def test_dollar():
    assert extract_budget("Budget $200") == "$200"

def test_pounds():
    assert extract_budget("Paid £300") == "£300"

def test_range():
    assert extract_budget("Budget $100-$300") == "$100–$300"

def test_none():
    assert extract_budget("Budget not specified") is None
