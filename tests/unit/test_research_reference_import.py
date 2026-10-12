import pytest


def test_reference_import_requires_evidence_and_does_not_guess_tracking_links():
    from scripts.register_investment_reference import ReferenceImport
    with pytest.raises(ValueError):
        ReferenceImport.model_validate({"indices": [{"id": "hs300", "providerSymbol": "sh000300"}]})
    data = ReferenceImport.model_validate({"indices": [{"id": "hs300", "providerSymbol": "sh000300", "sourceUri": "https://example.org/index/000300", "publishHour": 18}], "links": []})
    assert data.indices[0].publish_hour == 18
    assert data.links == []
