"""Тесты трассировки LLM-вызовов."""

import json
import tempfile
from pathlib import Path

from magistry_sim.tracing import LLMTracer, TracingLLMProvider
from magistry_sim.llm import MockLLMProvider, LLMResponse


class TestLLMTracer:
    """Запись и чтение трасс."""

    def test_record_span(self):
        tracer = LLMTracer()
        tracer.record(
            role="agent",
            agent_id="off_1",
            round_num=0,
            system="sys",
            user="usr",
            response="resp",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert len(tracer.spans) == 1
        span = tracer.spans[0]
        assert span["role"] == "agent"
        assert span["agent_id"] == "off_1"
        assert span["model"] == "gpt-4o"

    def test_save_jsonl(self):
        tracer = LLMTracer()
        tracer.record(
            role="arbiter", agent_id="", round_num=1,
            system="s", user="u", response="r",
            model="gpt-4o-mini", usage={},
        )
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = Path(f.name)
        tracer.save_jsonl(path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["role"] == "arbiter"
        path.unlink()

    def test_total_tokens(self):
        tracer = LLMTracer()
        tracer.record(
            role="agent", agent_id="", round_num=0,
            system="", user="", response="",
            model="m", usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        tracer.record(
            role="arbiter", agent_id="", round_num=0,
            system="", user="", response="",
            model="m", usage={"prompt_tokens": 20, "completion_tokens": 10},
        )
        assert tracer.total_tokens == 180


class TestTracingLLMProvider:
    """Обёртка провайдера с трассировкой."""

    def test_generate_records_span(self):
        mock = MockLLMProvider()
        tracer = LLMTracer()
        provider = TracingLLMProvider(
            inner=mock, tracer=tracer, role="agent", agent_id="off_1",
        )
        resp = provider.generate(system="sys", user="usr")
        assert isinstance(resp, LLMResponse)
        assert len(tracer.spans) == 1

    def test_round_num_updates(self):
        mock = MockLLMProvider()
        tracer = LLMTracer()
        provider = TracingLLMProvider(
            inner=mock, tracer=tracer, role="agent",
        )
        provider.round_num = 3
        provider.generate(system="s", user="u")
        assert tracer.spans[0]["round_num"] == 3
