from pathlib import Path

import pytest

from server.io.workspace import Workspace, safe_filename


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('report.pdf', 'report.pdf'),
        ('../../etc/passwd', 'passwd'),
        ('C:\\Users\\x\\doc.docx', 'doc.docx'),
        ('a<b>:c?.txt', 'a_b__c_.txt'),
        ('bad\x00name.md', 'bad_name.md'),
        ('...', 'file'),
        ('', 'file'),
        (' .hidden ', 'hidden'),
    ],
)
def test_safe_filename(name: str, expected: str):
    assert safe_filename(name) == expected


def test_safe_filename_truncates_but_keeps_extension():
    name = safe_filename('x' * 300 + '.docx')

    assert len(name) == 128
    assert name.endswith('.docx')


def test_new_files_never_collide(workspace: Workspace):
    first, second = workspace.new_file('a.md'), workspace.new_file('a.md')

    assert first != second
    assert first.name == second.name == 'a.md'
    assert first.parent.parent == workspace.path


def test_exit_removes_directory(tmp_path: Path):
    with Workspace(tmp_path) as workspace:
        workspace.new_file('a').write_text('x')

    assert not workspace.path.exists()


def test_kept_workspace_survives_exit_until_cleanup(tmp_path: Path):
    with Workspace(tmp_path) as workspace:
        workspace.keep()

    assert workspace.path.exists()
    workspace.cleanup()
    assert not workspace.path.exists()


def test_kept_workspace_is_removed_on_error(tmp_path: Path):
    with pytest.raises(RuntimeError), Workspace(tmp_path) as workspace:
        workspace.keep()
        raise RuntimeError

    assert not workspace.path.exists()
