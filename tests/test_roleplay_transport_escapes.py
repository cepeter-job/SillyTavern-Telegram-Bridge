"""Escaped model prose must be readable without altering literal code."""

from bridge.roleplay_format import normalize_roleplay_transport


def test_escaped_paragraphs_and_dialogue_render_as_newlines_and_quotes():
    source = '*She pauses.*\\n\\n\\"Coffee,\\" *she says.*\\n\\n\\"Ready?\\"'

    assert normalize_roleplay_transport(source) == '*She pauses.*\n\n"Coffee," *she says.*\n\n"Ready?"'


def test_escaped_newlines_preserve_code_and_literal_paths():
    code = '```json\n{"text":"a\\nb"}\n```'
    source = f'*She pauses.*\\n\\n\\"Coffee,\\"\n{code} C:\\new\\name'

    result = normalize_roleplay_transport(source)

    assert result.startswith('*She pauses.*\n\n"Coffee,"')
    assert code in result
    assert "C:\\new\\name" in result


def test_escaped_single_line_after_narration_becomes_newline():
    source = '*She turns.*\\n\\"Come with me.\\"'

    assert normalize_roleplay_transport(source) == '*She turns.*\n"Come with me."'


def test_plain_literal_path_is_not_decoded():
    source = "C:\\new\\name"

    assert "C:\\new\\name" in normalize_roleplay_transport(source)


def test_normalizing_escaped_prose_twice_is_stable():
    source = '*She pauses.*\\n\\n\\"Coffee,\\"'

    once = normalize_roleplay_transport(source)

    assert normalize_roleplay_transport(once) == once
