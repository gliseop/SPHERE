"""Генерация полнотекстовых документов по ГОСТ через LLM."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magistry_sim.llm import LLMProvider


class DocType(str, Enum):
    """Тип создаваемого документа."""

    MEMO = "memo"
    TECHNICAL_SPECIFICATION = "technical_specification"
    PROTOCOL = "protocol"
    COMMERCIAL_PROPOSAL = "commercial_proposal"
    AUDIT_REPORT = "audit_report"
    NEWSPAPER_ARTICLE = "newspaper_article"
    TELEGRAM_POST = "telegram_post"
    COMPLAINT = "complaint"
    COURT_FILING = "court_filing"


_GOST_INSTRUCTIONS: dict[DocType, str] = {
    DocType.MEMO: (
        "Оформи как служебную записку по ГОСТ Р 7.0.97-2016. "
        "Обязательные реквизиты: организация, дата, номер, адресат, "
        "заголовок, текст, подпись."
    ),
    DocType.TECHNICAL_SPECIFICATION: (
        "Оформи как техническое задание по ГОСТ 34.602-2020. "
        "Структура: общие сведения, назначение, требования, состав "
        "работ, контроль и приемка."
    ),
    DocType.PROTOCOL: (
        "Оформи как протокол по ГОСТ Р 7.0.97-2016: дата, номер, место, "
        "СЛУШАЛИ, ВЫСТУПИЛИ, ПОСТАНОВИЛИ, подписи."
    ),
    DocType.COMMERCIAL_PROPOSAL: (
        "Оформи как коммерческое предложение с реквизитами, условиями "
        "поставки и стоимостью."
    ),
    DocType.AUDIT_REPORT: (
        "Оформи как аудиторский отчет с основанием проверки, фактами, "
        "выводами и рекомендациями."
    ),
    DocType.NEWSPAPER_ARTICLE: (
        "Оформи как газетную статью: заголовок, лид, фактура, цитаты, вывод."
    ),
    DocType.TELEGRAM_POST: (
        "Оформи как пост в Telegram: емко, эмоционально, 500-1500 символов."
    ),
    DocType.COMPLAINT: (
        "Оформи как жалобу по ГОСТ Р 7.0.97-2016: адресат, заявитель, "
        "суть нарушений, требования, подпись."
    ),
    DocType.COURT_FILING: (
        "Оформи как исковое заявление: суд, стороны, обстоятельства, "
        "правовое обоснование, требования."
    ),
}

_BASE_SYSTEM = (
    "Ты генератор документов для симуляции муниципального управления. "
    "Создавай полный реалистичный текст на русском языке."
)


@dataclass
class GeneratedDocument:
    """Результат генерации документа."""

    doc_id: str
    doc_type: DocType
    title: str
    content: str
    case_id: str = ""


class DocumentForge:
    """Генератор документов по ГОСТ через LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        self._counter = 0

    def generate(
        self,
        doc_type: DocType,
        author_id: str,
        author_name: str,
        author_position: str,
        context: str,
        case_id: str = "",
        title: str = "",
    ) -> GeneratedDocument:
        """Сгенерировать документ через LLM."""
        del author_id
        self._counter += 1
        doc_id = f"DOC-{self._counter:04d}"

        gost = _GOST_INSTRUCTIONS.get(doc_type, "")
        system_prompt = (
            f"{_BASE_SYSTEM}\n\n"
            f"Требования к оформлению по ГОСТ:\n{gost}\n\n"
            f"Автор: {author_name}, {author_position}."
        )
        user_prompt = (
            f"Составь документ типа «{doc_type.value}».\n\n"
            f"Контекст:\n{context}"
        )
        if case_id:
            user_prompt += f"\n\nДело: {case_id}"

        response = self._llm.generate(
            system=system_prompt,
            user=user_prompt,
        )
        content = response.text.strip()

        if not title:
            first_line = content.splitlines()[0].strip() if content else ""
            title = first_line[:120] if first_line else f"Документ {doc_id}"

        return GeneratedDocument(
            doc_id=doc_id,
            doc_type=doc_type,
            title=title,
            content=content,
            case_id=case_id,
        )
