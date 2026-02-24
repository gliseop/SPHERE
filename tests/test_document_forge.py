"""Тесты DocumentForge — генерация документов по ГОСТ."""

from unittest.mock import MagicMock

from magistry_sim.document_forge import DocType, DocumentForge


def test_doc_types():
    assert DocType.MEMO.value == "memo"
    assert (
        DocType.TECHNICAL_SPECIFICATION.value
        == "technical_specification"
    )
    assert DocType.PROTOCOL.value == "protocol"
    assert DocType.COMMERCIAL_PROPOSAL.value == "commercial_proposal"
    assert DocType.AUDIT_REPORT.value == "audit_report"
    assert DocType.NEWSPAPER_ARTICLE.value == "newspaper_article"
    assert DocType.TELEGRAM_POST.value == "telegram_post"


def test_generate_document():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = MagicMock(
        text="УТВЕРЖДАЮ\nКозлов И.М.\nСлужебная записка..."
    )
    forge = DocumentForge(llm=mock_llm)
    result = forge.generate(
        doc_type=DocType.MEMO,
        author_id="off_1",
        author_name="Козлов И.М.",
        author_position="Начальник отдела обеспечения",
        context="Требуется закупка серверного оборудования",
        case_id="D-001",
    )
    assert result.doc_id.startswith("DOC-")
    assert result.doc_type == DocType.MEMO
    assert "УТВЕРЖДАЮ" in result.content
    mock_llm.generate.assert_called_once()


def test_gost_prompt_contains_requirements():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = MagicMock(text="doc text")
    forge = DocumentForge(llm=mock_llm)
    forge.generate(
        doc_type=DocType.TECHNICAL_SPECIFICATION,
        author_id="off_1",
        author_name="Козлов И.М.",
        author_position="Начальник",
        context="Закупка серверов",
    )
    call_args = mock_llm.generate.call_args
    system_prompt = call_args.kwargs.get("system", "")
    assert "ГОСТ" in system_prompt


def test_auto_title_from_first_line():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = MagicMock(
        text="СЛУЖЕБНАЯ ЗАПИСКА\nО необходимости закупки оборудования\n..."
    )
    forge = DocumentForge(llm=mock_llm)
    result = forge.generate(
        doc_type=DocType.MEMO,
        author_id="off_1",
        author_name="Козлов И.М.",
        author_position="Начальник",
        context="Закупка серверов",
    )
    assert result.title == "СЛУЖЕБНАЯ ЗАПИСКА"


def test_empty_llm_response():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = MagicMock(text="")
    forge = DocumentForge(llm=mock_llm)
    result = forge.generate(
        doc_type=DocType.MEMO,
        author_id="off_1",
        author_name="Козлов И.М.",
        author_position="Начальник",
        context="Тест",
    )
    assert result.doc_id.startswith("DOC-")
    assert result.title.startswith("Документ DOC-")
