from server.utils.openapi import inline_refs


def test_inlines_nested_refs_and_drops_defs():
    schema = {
        'properties': {'a': {'$ref': '#/$defs/A'}, 'list': {'items': [{'$ref': '#/$defs/B'}]}},
        '$defs': {'A': {'properties': {'b': {'$ref': '#/$defs/B'}}}, 'B': {'type': 'string'}},
    }

    assert inline_refs(schema) == {
        'properties': {
            'a': {'properties': {'b': {'type': 'string'}}},
            'list': {'items': [{'type': 'string'}]},
        }
    }
