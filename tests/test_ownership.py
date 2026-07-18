from object_datamosh.core.ownership import is_owned, mark_owned, owned_name


def test_ownership_contract_prefixes_and_tags_extension_data() -> None:
    properties: dict[str, object] = {}

    mark_owned(properties)

    assert owned_name("Feedback") == "ODM_Feedback"
    assert owned_name("ODM_Feedback") == "ODM_Feedback"
    assert is_owned(properties)
