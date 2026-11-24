import pickle
from pathlib import Path
from typing import Any

import pytest
from papyrus_sdk import APIError, ChunkingOptions, FormatGroup, MarkdownResponse, ProblemIssue, Result
from pydantic import ValidationError

ENVELOPE: dict[str, Any] = {
    'source': {'uri': 'file://file', 'name': 'a.txt', 'size': 2, 'mimeType': 'text/plain', 'group': 'text'},
    'options': {},
    'result': {
        'name': 'a.md',
        'size': 3,
        'mimeType': 'text/markdown',
        'extension': 'md',
        'label': 'markdown',
        'group': 'markup',
        'encoding': 'utf-8',
        'content': 'hié',
    },
}


def _result(**changes: Any) -> Result:
    return Result.model_validate({**ENVELOPE['result'], **changes})


def test_wire_names_are_read_into_python_names():
    response = MarkdownResponse.model_validate(ENVELOPE)

    assert response.source.mime_type == 'text/plain'
    assert response.result.group is FormatGroup.MARKUP
    assert response.chunks is None


def test_unknown_fields_are_ignored():
    source = {**ENVELOPE['source'], 'sha256': 'abc'}

    response = MarkdownResponse.model_validate({**ENVELOPE, 'source': source, 'options': {'quality': 3}, 'links': []})

    assert response.result.text == 'hié'


def test_unknown_enum_values_are_rejected():
    with pytest.raises(ValidationError, match='group'):
        _result(group='hologram')


def test_text_result(tmp_path: Path):
    result = _result()

    assert result.text == 'hié'
    assert result.data == 'hié'.encode()
    assert result.save(tmp_path / 'a.md').read_bytes() == 'hié'.encode()


def test_binary_result(tmp_path: Path):
    result = _result(encoding='base64', content='iVBORw==')

    assert result.data == b'\x89PNG'
    assert result.save(str(tmp_path / 'a.png')).read_bytes() == b'\x89PNG'
    with pytest.raises(ValueError, match='binary'):
        _ = result.text


def test_empty_content_is_content():
    assert _result(content='').text == ''


def test_delivered_result_has_no_content(tmp_path: Path):
    result = _result(uri='s3://prod/a.md', encoding=None, content=None)

    for read in (lambda: result.data, lambda: result.text, lambda: result.save(tmp_path / 'a.md')):
        with pytest.raises(ValueError, match='s3://prod/a.md'):
            read()
    assert not (tmp_path / 'a.md').exists()


def test_chunking_options_accept_python_names():
    options = ChunkingOptions(max_tokens=128)

    assert options.model_dump(mode='json', by_alias=True, exclude_unset=True) == {'maxTokens': 128}


def test_api_error_is_readable_and_picklable():
    issue = ProblemIssue(loc=['options', 'page'], msg='too small', type='greater_than_equal')
    error = APIError(422, 'validation_error', 'request validation failed', [issue], 'req-1', 2.0)

    copy = pickle.loads(pickle.dumps(error))

    assert str(copy) == '422 validation_error: request validation failed (options.page: too small)'
    assert (copy.code, copy.errors, copy.request_id, copy.retry_after) == ('validation_error', [issue], 'req-1', 2.0)
    assert str(APIError(413)) == '413 error'
