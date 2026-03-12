"""Скрипт обогащения тестовых данных S1_G1_seed42.

Заменяет заглушки ``content: "msg"`` на осмысленные русскоязычные реплики,
добавляет поле ``reason`` к ``reputation_modified``, вводит дополнительных
агентов (семья, друзья, журналист, активист) и генерирует события их
взаимодействий.
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results"

EVENTS_FILE = RESULTS_DIR / "S1_G1_seed42_events.jsonl"
NAMES_FILE = RESULTS_DIR / "S1_G1_seed42_names.json"

# ---------- Новые агенты ----------

NEW_AGENTS: dict[str, str] = {
    "fam_wife": "Козлова А.М.",
    "fam_son": "Козлов И.",
    "soc_journalist": "Журналист Сидоров",
    "soc_activist": "Активист Иванова",
    "biz_friend": "ИП Кузнецов",
}

# ---------- Словарь сообщений по ролям ----------

MESSAGES: dict[str, list[str]] = {
    "off_mayor": [
        "Предлагаю обсудить условия контракта на реконструкцию моста в закрытом формате.",
        "Необходимо ускорить процедуру согласования — сроки поджимают.",
        "Прошу подготовить документы к следующему заседанию совета.",
        "Есть вопросы по распределению бюджета на второй квартал.",
        "Давайте встретимся после заседания, есть конфиденциальный вопрос.",
    ],
    "off_deputy": [
        "Получил результаты проверки — есть замечания по документации.",
        "Рекомендую привлечь дополнительных подрядчиков для ускорения работ.",
        "Согласовал предварительный план с юридическим отделом.",
        "Нужно пересмотреть критерии отбора участников конкурса.",
        "Отчёт по муниципальным закупкам готов к рассмотрению.",
    ],
    "off_clerk": [
        "Документы по тендеру подготовлены, жду подписи.",
        "Заявка от ООО «Альфа» соответствует формальным требованиям.",
        "Передаю справку по бюджетным остаткам за прошлый период.",
        "Внёс правки в протокол заседания по вашим замечаниям.",
        "Могу подготовить дополнительную сводку при необходимости.",
    ],
    "biz_alpha": [
        "Готовы приступить к работам немедленно после подписания договора.",
        "Направляем коммерческое предложение на сумму 12.5 млн руб.",
        "Просим рассмотреть возможность авансирования первого этапа.",
        "Наша компания имеет успешный опыт аналогичных проектов.",
        "Предлагаем встретиться для обсуждения технических деталей.",
    ],
    "biz_beta": [
        "Снижаем цену на 15% для участия в конкурсе.",
        "Предлагаем альтернативное техническое решение — дешевле и быстрее.",
        "Готовы предоставить банковскую гарантию в течение трёх дней.",
        "Наши субподрядчики подтвердили готовность к работе.",
        "Есть интересное предложение по оптимизации затрат на проект.",
    ],
    "biz_gamma": [
        "Направляем обновлённую смету с учётом текущих цен на материалы.",
        "Просим продлить срок подачи конкурсной заявки на неделю.",
        "У нас есть вопросы по техническому заданию пункт 3.2.",
        "Подтверждаем участие в тендере на реконструкцию.",
        "Готовы обсудить условия субподряда с победителем конкурса.",
    ],
    "aud_inspector": [
        "Зафиксировано несоответствие в документации по закупке #47.",
        "Запрашиваю доступ к протоколам закрытых совещаний за январь.",
        "Обнаружены признаки аффилированности между участниками конкурса.",
        "Необходима дополнительная проверка финансовых потоков.",
        "Прошу предоставить пояснения по отклонению заявки ООО «Гамма».",
    ],
    "fam_wife": [
        "Сегодня муж опять задержался на работе — говорит, важное совещание.",
        "Заметила новую машину у подъезда, кажется служебную.",
    ],
    "fam_son": [
        "Отец просил передать папку с документами в администрацию.",
        "Видел отца в ресторане с незнакомым бизнесменом.",
    ],
    "soc_journalist": [
        "Пишу статью о муниципальных закупках — есть подозрительные схемы.",
        "Получил анонимную наводку на связь мэра с подрядчиком.",
        "Публикую расследование: три тендера — один бенефициар.",
    ],
    "soc_activist": [
        "Подаю запрос на раскрытие информации по проекту реконструкции.",
        "Организую общественные слушания по бюджету — приглашаю всех.",
        "Обнаружила расхождение между обещаниями мэра и реальными расходами.",
    ],
    "biz_friend": [
        "Слышал, что конкурс уже решён — стоит ли подавать заявку?",
        "Могу свести тебя с нужными людьми в администрации.",
    ],
}

# Индексы для выбора сообщений без повторов
_msg_idx: dict[str, int] = {}


def _pick_message(agent_id: str) -> str:
    """Выбрать следующее сообщение для агента по кругу.

    Args:
        agent_id: Идентификатор агента.

    Returns:
        Текст сообщения.
    """
    prefix = agent_id.split("_")[0] + "_" + agent_id.split("_")[1] if "_" in agent_id else agent_id
    msgs = MESSAGES.get(prefix) or MESSAGES.get(agent_id, ["[сообщение]"])
    idx = _msg_idx.get(agent_id, 0)
    msg = msgs[idx % len(msgs)]
    _msg_idx[agent_id] = idx + 1
    return msg


# ---------- Причины изменения репутации ----------

REPUTATION_REASONS: dict[str, list[str]] = {
    "off_mayor": [
        "Приватная встреча с представителем бизнеса",
        "Попытка ускорить согласование в обход процедуры",
        "Закрытое совещание без протокола",
        "Подозрительная связь с подрядчиком",
    ],
    "off_deputy": [
        "Публичное выступление с отчётом",
        "Содействие в проверке документации",
    ],
    "off_clerk": [
        "Передача документов без регистрации",
        "Нарушение порядка обработки заявок",
    ],
    "biz_alpha": [
        "Завышение сметной стоимости контракта",
        "Целенаправленная атака конкурентов",
        "Подозрение в сговоре с чиновником",
    ],
    "biz_beta": [
        "Демпинг цен для вытеснения конкурентов",
        "Недостоверные сведения в конкурсной заявке",
        "Положительная оценка субподрядчиков",
    ],
    "biz_gamma": [
        "Обоснованная жалоба на условия конкурса",
        "Прозрачная финансовая отчётность",
    ],
    "aud_inspector": [
        "Эффективное выявление нарушений",
        "Давление со стороны проверяемых лиц",
    ],
}

_reason_idx: dict[str, int] = {}


def _pick_reason(agent_id: str) -> str:
    """Выбрать причину изменения репутации.

    Args:
        agent_id: Идентификатор агента-цели.

    Returns:
        Текстовая причина.
    """
    reasons = REPUTATION_REASONS.get(agent_id, ["Системная корректировка"])
    idx = _reason_idx.get(agent_id, 0)
    reason = reasons[idx % len(reasons)]
    _reason_idx[agent_id] = idx + 1
    return reason


# ---------- Дополнительные события для новых агентов ----------


def _generate_new_agent_events() -> list[dict]:
    """Создать события взаимодействий для новых агентов.

    Returns:
        Список событий в формате JSONL-словарей.
    """
    events = []

    # Раунд 2: семья мэра, друг бизнесмена
    events.append({
        "round": 2, "event_type": "message_sent", "agent_id": "fam_wife",
        "payload": {"to_id": "off_mayor", "private": True,
                    "content": _pick_message("fam_wife")},
        "timestamp": "2024-01-02T04:00:00",
    })
    events.append({
        "round": 2, "event_type": "message_sent", "agent_id": "biz_friend",
        "payload": {"to_id": "biz_alpha", "private": False,
                    "content": _pick_message("biz_friend")},
        "timestamp": "2024-01-02T04:10:00",
    })
    events.append({
        "round": 2, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "fam_wife", "agent_b": "off_mayor", "delta": 0.35},
        "timestamp": "2024-01-02T04:20:00",
    })
    events.append({
        "round": 2, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "biz_friend", "agent_b": "biz_alpha", "delta": 0.55},
        "timestamp": "2024-01-02T04:25:00",
    })

    # Раунд 3: журналист и активист появляются
    events.append({
        "round": 3, "event_type": "message_sent", "agent_id": "soc_journalist",
        "payload": {"to_id": "aud_inspector", "private": False,
                    "content": _pick_message("soc_journalist")},
        "timestamp": "2024-01-03T04:00:00",
    })
    events.append({
        "round": 3, "event_type": "message_sent", "agent_id": "soc_activist",
        "payload": {"to_id": "off_deputy", "private": False,
                    "content": _pick_message("soc_activist")},
        "timestamp": "2024-01-03T04:10:00",
    })
    events.append({
        "round": 3, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "soc_journalist", "agent_b": "aud_inspector", "delta": 0.4},
        "timestamp": "2024-01-03T04:20:00",
    })
    events.append({
        "round": 3, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "soc_activist", "agent_b": "off_deputy", "delta": 0.3},
        "timestamp": "2024-01-03T04:25:00",
    })
    events.append({
        "round": 3, "event_type": "self_reflection", "agent_id": "soc_journalist",
        "payload": {"content": "Получил данные от источника в администрации. Три последних тендера выиграны одной группой компаний. Нужно проверить связи мэра."},
        "timestamp": "2024-01-03T04:30:00",
    })

    # Раунд 4: активные взаимодействия новых агентов
    events.append({
        "round": 4, "event_type": "message_sent", "agent_id": "fam_son",
        "payload": {"to_id": "off_mayor", "private": True,
                    "content": _pick_message("fam_son")},
        "timestamp": "2024-01-04T04:00:00",
    })
    events.append({
        "round": 4, "event_type": "message_sent", "agent_id": "soc_journalist",
        "payload": {"to_id": "biz_alpha", "private": False,
                    "content": _pick_message("soc_journalist")},
        "timestamp": "2024-01-04T04:10:00",
    })
    events.append({
        "round": 4, "event_type": "message_sent", "agent_id": "biz_friend",
        "payload": {"to_id": "off_clerk", "private": True,
                    "content": _pick_message("biz_friend")},
        "timestamp": "2024-01-04T04:20:00",
    })
    events.append({
        "round": 4, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "fam_son", "agent_b": "off_mayor", "delta": 0.2},
        "timestamp": "2024-01-04T04:30:00",
    })
    events.append({
        "round": 4, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "soc_journalist", "agent_b": "biz_alpha", "delta": 0.15},
        "timestamp": "2024-01-04T04:35:00",
    })
    events.append({
        "round": 4, "event_type": "world_event", "agent_id": "system",
        "payload": {"type": "social_media",
                    "narrative": "В социальных сетях появилась публикация @journalist_sidorov: «Кто стоит за серией тендеров в мэрии? Расследование.» Пост набирает просмотры."},
        "timestamp": "2024-01-04T04:40:00",
    })

    # Раунд 5: аудитор анализирует OSINT
    events.append({
        "round": 5, "event_type": "self_reflection", "agent_id": "aud_inspector",
        "payload": {"content": "Обнаружена публикация в соцсети: @journalist_sidorov упоминает встречу off_mayor с biz_alpha. Совпадает с данными моего наблюдения — подтверждает гипотезу о неформальных связях."},
        "timestamp": "2024-01-05T04:00:00",
    })
    events.append({
        "round": 5, "event_type": "message_sent", "agent_id": "soc_activist",
        "payload": {"to_id": "soc_journalist", "private": False,
                    "content": _pick_message("soc_activist")},
        "timestamp": "2024-01-05T04:10:00",
    })
    events.append({
        "round": 5, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "soc_activist", "agent_b": "soc_journalist", "delta": 0.6},
        "timestamp": "2024-01-05T04:20:00",
    })
    events.append({
        "round": 5, "event_type": "reputation_modified", "agent_id": "system",
        "payload": {"target": "soc_journalist", "delta": 0.8,
                    "reason": "Публикация резонансного расследования"},
        "timestamp": "2024-01-05T04:30:00",
    })

    # Раунд 6: нарастание давления
    events.append({
        "round": 6, "event_type": "message_sent", "agent_id": "soc_journalist",
        "payload": {"to_id": "off_mayor", "private": False,
                    "content": _pick_message("soc_journalist")},
        "timestamp": "2024-01-06T04:00:00",
    })
    events.append({
        "round": 6, "event_type": "message_sent", "agent_id": "soc_activist",
        "payload": {"to_id": "aud_inspector", "private": False,
                    "content": _pick_message("soc_activist")},
        "timestamp": "2024-01-06T04:10:00",
    })
    events.append({
        "round": 6, "event_type": "message_sent", "agent_id": "fam_wife",
        "payload": {"to_id": "fam_son", "private": True,
                    "content": _pick_message("fam_wife")},
        "timestamp": "2024-01-06T04:20:00",
    })
    events.append({
        "round": 6, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "soc_journalist", "agent_b": "off_mayor", "delta": -0.3},
        "timestamp": "2024-01-06T04:25:00",
    })
    events.append({
        "round": 6, "event_type": "graph_updated", "agent_id": "system",
        "payload": {"agent_a": "fam_wife", "agent_b": "fam_son", "delta": 0.5},
        "timestamp": "2024-01-06T04:30:00",
    })
    events.append({
        "round": 6, "event_type": "reputation_modified", "agent_id": "system",
        "payload": {"target": "off_mayor", "delta": -0.6,
                    "reason": "Публичное расследование журналиста"},
        "timestamp": "2024-01-06T04:35:00",
    })

    # Раунд 7: финальные действия
    events.append({
        "round": 7, "event_type": "message_sent", "agent_id": "soc_activist",
        "payload": {"to_id": "off_mayor", "private": False,
                    "content": "Требуем публичного отчёта о расходовании средств на реконструкцию!"},
        "timestamp": "2024-01-07T04:00:00",
    })
    events.append({
        "round": 7, "event_type": "self_reflection", "agent_id": "soc_journalist",
        "payload": {"content": "Итог расследования: выявлена устойчивая схема распределения контрактов через аффилированных подрядчиков. Материал передан в редакцию."},
        "timestamp": "2024-01-07T04:10:00",
    })
    events.append({
        "round": 7, "event_type": "reputation_modified", "agent_id": "system",
        "payload": {"target": "soc_activist", "delta": 0.5,
                    "reason": "Активная гражданская позиция"},
        "timestamp": "2024-01-07T04:20:00",
    })

    return events


def enrich() -> None:
    """Обогатить существующий JSONL реальными данными."""
    # Прочитать существующие события
    with open(EVENTS_FILE, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    existing_events = [json.loads(line) for line in lines]

    # Обогатить существующие события
    for event in existing_events:
        et = event.get("event_type")
        aid = event.get("agent_id", "")

        # Заменить content: "msg" на осмысленные реплики
        if et == "message_sent":
            payload = event["payload"]
            if payload.get("content") == "msg":
                payload["content"] = _pick_message(aid)

        # Добавить reason к reputation_modified
        if et == "reputation_modified":
            payload = event["payload"]
            target = payload.get("target", "")
            if "reason" not in payload:
                payload["reason"] = _pick_reason(target)

    # Добавить события новых агентов
    new_events = _generate_new_agent_events()

    # Объединить и отсортировать по раунду и timestamp
    all_events = existing_events + new_events
    all_events.sort(key=lambda e: (e.get("round", 0), e.get("timestamp", "")))

    # Записать обратно
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        for event in all_events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # Обновить names.json
    with open(NAMES_FILE, encoding="utf-8") as f:
        names = json.loads(f.read())

    names.update(NEW_AGENTS)

    with open(NAMES_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(names, ensure_ascii=False, indent=2) + "\n")

    print(f"Обогащено событий: {len(existing_events)} существующих + {len(new_events)} новых = {len(all_events)}")
    print(f"Агентов в names.json: {len(names)}")


if __name__ == "__main__":
    enrich()
