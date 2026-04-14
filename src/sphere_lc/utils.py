"""Мелкие утилиты SPHERE-LC."""

from __future__ import annotations

import re


_WS_RE = re.compile(r"\s+")
_TYPED_NAME_RE = re.compile(r"^(agent|org|chan|work|vote):", flags=re.IGNORECASE)
_ROLE_KEYWORDS = {
    "acting",
    "admin",
    "advisor",
    "analyst",
    "auditor",
    "boss",
    "candidate",
    "chief",
    "consultant",
    "contractor",
    "coordinator",
    "deputy",
    "department",
    "director",
    "employee",
    "expert",
    "head",
    "inspector",
    "journalist",
    "lawyer",
    "legal",
    "manager",
    "mayor",
    "observer",
    "officer",
    "official",
    "procurement",
    "reporter",
    "reviewer",
    "secretary",
    "section",
    "specialist",
    "supervisor",
    "witness",
    "администратор",
    "администрации",
    "аудитор",
    "ведущий",
    "временный",
    "врио",
    "глава",
    "директор",
    "журналист",
    "заместитель",
    "замглавы",
    "инспектор",
    "исполняющий",
    "канал",
    "консультант",
    "контрагент",
    "координатор",
    "куратор",
    "начальник",
    "наблюдатель",
    "организация",
    "отдел",
    "подрядчик",
    "помощник",
    "председатель",
    "пресс",
    "руководитель",
    "секретарь",
    "служба",
    "сотрудник",
    "специалист",
    "управление",
    "эксперт",
    "юрист",
}
_ROLE_PREFIXES = (
    "администр",
    "аудитор",
    "директор",
    "журналист",
    "замест",
    "закуп",
    "канал",
    "консульт",
    "контраг",
    "координ",
    "началь",
    "наблюд",
    "отдел",
    "подряд",
    "руковод",
    "секрет",
    "сотруд",
    "специал",
    "свидетел",
    "управл",
    "эксперт",
    "юрист",
)
_NAME_TOKEN_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def normalize_whitespace(text: str) -> str:
    """Нормализовать пробелы в свободном тексте."""
    return _WS_RE.sub(" ", (text or "").strip())


def normalize_agent_display_name(name: str, *, fallback: str = "") -> str:
    """Привести display-name агента к человеко-читаемой форме."""
    raw = (name or "").strip().strip("\"'`")
    if not raw:
        raw = (fallback or "").strip()
    if _TYPED_NAME_RE.match(raw):
        raw = raw.split(":", 1)[1]
    raw = raw.replace("_", " ").replace("-", " ")
    raw = normalize_whitespace(raw)
    if not raw:
        return ""

    parts: list[str] = []
    for part in raw.split(" "):
        if not part:
            continue
        if part.isascii() and part.islower() and any(ch.isalpha() for ch in part):
            parts.append(part.capitalize())
            continue
        parts.append(part)
    return " ".join(parts).strip()


def social_name_key(text: str) -> str:
    """Нормализованный ключ имени/ярлыка для нечёткого сравнения."""
    parts = [p for p in _NAME_TOKEN_RE.split((text or "").casefold()) if p]
    return "_".join(sorted(parts))


def looks_like_machine_name(name: str) -> bool:
    """Похоже ли имя на машинный slug/ID, а не на display-name."""
    raw = (name or "").strip()
    if not raw:
        return True
    if _TYPED_NAME_RE.match(raw):
        return True
    if "_" in raw:
        return True
    if raw.count(":") >= 1:
        return True
    return False


def looks_like_role_label(name: str) -> bool:
    """Похоже ли значение на должность/роль, а не на конкретного человека."""
    normalized = normalize_agent_display_name(name)
    if not normalized:
        return True

    tokens = [t for t in _NAME_TOKEN_RE.split(normalized.casefold()) if t]
    if not tokens:
        return True

    def _is_role_token(token: str) -> bool:
        if token in _ROLE_KEYWORDS:
            return True
        return any(token.startswith(prefix) for prefix in _ROLE_PREFIXES)

    keyword_hits = sum(1 for token in tokens if _is_role_token(token))
    non_keyword_tokens = [token for token in tokens if not _is_role_token(token)]
    if keyword_hits and not non_keyword_tokens:
        return True
    if keyword_hits >= max(1, len(tokens) - 1) and len(non_keyword_tokens) <= 1:
        return True
    return False


def redact_numbers(obj: object) -> object:
    """Заменить все числовые значения на маркер `<num>`.

    Используется, чтобы не передавать LLM "сырые числа" из внутренних структур.
    """
    if isinstance(obj, dict):
        return {k: redact_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_numbers(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return "<num>"
    return obj
