import anthropic
import pytest
from anthropic.types import Message, MessageTokensCount

from autoplay.core.agent.budget import Usage
from autoplay.core.agent.client import AnthropicModel
from autoplay.core.agent.config import load_agent_config
from autoplay.core.agent.loop import ModelError

RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [
        {"type": "text", "text": "Opening the bag."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "press",
            "input": {"button": "X"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 12,
        "output_tokens": 34,
        "cache_read_input_tokens": 56,
        "cache_creation_input_tokens": 78,
    },
}


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []
        self.counts = []

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        if self.error is not None:
            raise self.error
        return MessageTokensCount(input_tokens=4321)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeAnthropic:
    def __init__(self, **kwargs):
        self.messages = FakeMessages(**kwargs)
        self.options = []

    def with_options(self, **options):
        self.options.append(options)
        return self


def test_request_shape_and_reply_mapping():
    api = FakeAnthropic(response=Message.model_validate(RESPONSE))
    model = AnthropicModel(load_agent_config(), client=api)
    tools = [{"name": "press", "description": "d", "input_schema": {"type": "object"}}]
    messages = [{"role": "user", "content": "go"}]
    reply = model.send("SYSTEM", tools, messages)
    (request,) = api.messages.requests
    assert api.options == [{"max_retries": 0}]
    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == 3000
    assert request["output_config"] == {"effort": "medium"}
    assert request["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}
    ]
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["tools"] is tools and request["messages"] is messages
    assert not {"thinking", "temperature", "tool_choice", "fallbacks"} & request.keys()
    assert reply.stop_reason == "tool_use"
    assert reply.text == "Opening the bag."
    (call,) = reply.tool_calls
    assert (call.id, call.name, call.input) == ("toolu_1", "press", {"button": "X"})
    assert reply.usage == Usage(12, 34, 56, 78)
    assert reply.content == list(api.messages.response.content)
    assert reply.refusal_category is None


def test_refusal_category():
    data = {
        **RESPONSE,
        "content": [],
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"},
    }
    api = FakeAnthropic(response=Message.model_validate(data))
    reply = AnthropicModel(load_agent_config(), client=api).send("s", [], [])
    assert (reply.stop_reason, reply.refusal_category) == ("refusal", "cyber")


class Unreachable(anthropic.APIError):
    def __init__(self):
        Exception.__init__(self, "connection error")


def test_api_error_becomes_a_model_error():
    api = FakeAnthropic(error=Unreachable())
    with pytest.raises(ModelError, match="Unreachable: connection error"):
        AnthropicModel(load_agent_config(), client=api).send("s", [], [])


def test_count_tokens_counts_the_prompt_that_send_sends():
    api = FakeAnthropic(response=Message.model_validate(RESPONSE))
    model = AnthropicModel(load_agent_config(), client=api)
    tools, messages = [{"name": "t"}], [{"role": "user", "content": "go"}]
    assert model.count_tokens("SYSTEM", tools, messages) == 4321
    model.send("SYSTEM", tools, messages)
    (counted,), (sent,) = api.messages.counts, api.messages.requests
    assert "max_tokens" not in counted
    assert counted == {k: v for k, v in sent.items() if k != "max_tokens"}


def test_count_error_becomes_a_model_error():
    api = FakeAnthropic(error=Unreachable())
    with pytest.raises(ModelError, match="Unreachable"):
        AnthropicModel(load_agent_config(), client=api).count_tokens("s", [], [])
