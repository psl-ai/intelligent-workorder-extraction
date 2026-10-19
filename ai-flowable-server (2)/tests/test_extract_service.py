from app.services.extract_service import format_extract_response, normalize_extract_result


def test_multiple_enum_values_use_english_commas():
    result = normalize_extract_result(
        {
            "business_type": {
                "v": "咨询类、求助类",
                "r": 1,
                "c": 1,
                "o": ["consultation", "assistance"],
            }
        }
    )

    assert result["business_type"]["value"] == "咨询类,求助类"


def test_multiple_extract_config_values_use_english_commas_in_response():
    extract_config = [
        {
            "rec_type": "业务类型",
            "rec_type_en": "business_type",
            "multiple": True,
        },
        {
            "rec_type": "业务分类",
            "rec_type_en": "business_category",
            "multiple": True,
        },
    ]
    result = normalize_extract_result(
        {
            "business_type": {
                "v": "投诉类,建议类",
                "r": 1,
                "c": 1,
                "o": ["complaint", "suggestion"],
            },
            "business_category": {
                "v": "工资、公共信息",
                "r": 1,
                "c": 1,
                "o": ["wages", "public_information"],
            },
        },
        extract_config=extract_config,
    )

    response = format_extract_response(result, extract_config)

    assert response[0]["value"] == "投诉类,建议类"
    assert response[1]["value"] == "工资,公共信息"
