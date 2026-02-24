"""Тесты ConversationManager — управление тредами разговоров."""

from magistry_sim.conversation import (
    ChannelType,
    ConversationManager,
)


def test_create_thread():
    cm = ConversationManager()
    thread = cm.create_thread(
        initiator="off_1",
        recipient="biz_1",
        channel=ChannelType.TELEGRAM,
    )
    assert thread.thread_id.startswith("T-")
    assert thread.initiator == "off_1"
    assert thread.recipient == "biz_1"
    assert thread.channel == ChannelType.TELEGRAM
    assert thread.messages == []
    assert not thread.is_closed


def test_add_message():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.add_message(thread.thread_id, "off_1", "Привет, Сергей")
    assert len(thread.messages) == 1
    assert thread.messages[0].sender == "off_1"
    assert thread.messages[0].content == "Привет, Сергей"


def test_thread_max_messages():
    cm = ConversationManager(max_messages=3)
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.add_message(thread.thread_id, "off_1", "msg1")
    cm.add_message(thread.thread_id, "biz_1", "msg2")
    cm.add_message(thread.thread_id, "off_1", "msg3")
    assert thread.is_closed


def test_close_thread():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.PHONE)
    cm.close_thread(thread.thread_id)
    assert thread.is_closed


def test_channel_types():
    assert ChannelType.TELEGRAM.value == "telegram"
    assert ChannelType.PHONE.value == "phone"
    assert ChannelType.FACE_TO_FACE.value == "face_to_face"
    assert ChannelType.EMAIL.value == "email"
    assert ChannelType.OFFICIAL_DOC.value == "official_doc"


def test_get_thread_context():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.add_message(thread.thread_id, "off_1", "Привет")
    cm.add_message(thread.thread_id, "biz_1", "Здравствуйте")
    ctx = cm.get_thread_context(thread.thread_id)
    assert "off_1: Привет" in ctx
    assert "biz_1: Здравствуйте" in ctx


def test_add_message_after_close_raises():
    cm = ConversationManager()
    thread = cm.create_thread("off_1", "biz_1", ChannelType.TELEGRAM)
    cm.close_thread(thread.thread_id)
    import pytest
    with pytest.raises(ValueError, match="closed"):
        cm.add_message(thread.thread_id, "off_1", "hello")


def test_add_message_invalid_thread_raises():
    cm = ConversationManager()
    import pytest
    with pytest.raises(ValueError, match="not found"):
        cm.add_message("T-9999", "off_1", "hello")
