import anthropic

from autoplay.core.agent.budget import Usage
from autoplay.core.agent.config import AgentConfig
from autoplay.core.agent.loop import ModelError, ModelReply, ToolCall

REQUEST_TIMEOUT_S = 180.0
# The loop replaces old screenshots with a line of text, which is an edit of
# the history. A thinking block is bound to the conversation it was made in,
# and by default a request that replays one after such an edit is refused.
# With this setting the API leaves those blocks out instead; the model loses
# that earlier reasoning, which is the accepted price of the smaller history.
BINDING_BETA = "thinking-binding-controls-2026-08-01"
THINKING = {
    "type": "adaptive",
    "block_binding": {"prefix_mismatch_behavior": "drop_block"},
}


class AnthropicModel:
    """The model behind the Anthropic Messages API.

    The request never sets `temperature` or `tool_choice`, which the
    configured models reject or ignore, nor server-side fallbacks: a reply
    billed across two models could not be held to the run's budget."""

    def __init__(
        self,
        config: AgentConfig,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._config = config
        try:
            self._client = client or anthropic.Anthropic(
                api_key=api_key, timeout=REQUEST_TIMEOUT_S
            )
        except anthropic.AnthropicError as exc:
            raise ModelError(f"cannot set up the model client: {exc}") from exc

    def _prompt(self, system: str, tools: list[dict], messages: list[dict]) -> dict:
        return {
            "model": self._config.model,
            # One breakpoint after tools and system, which never change in a
            # run; the top-level one follows the end of the conversation.
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "tools": tools,
            "messages": messages,
            "output_config": {"effort": self._config.effort},
            "cache_control": {"type": "ephemeral"},
        }

    def count_tokens(self, system: str, tools: list[dict], messages: list[dict]) -> int:
        try:
            counted = self._client.messages.count_tokens(
                **self._prompt(system, tools, messages)
            )
        except anthropic.APIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        return counted.input_tokens

    def send(
        self,
        system: str,
        tools: list[dict],
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> ModelReply:
        try:
            # No retries: a request whose reply was lost may still be billed,
            # and a second one would spend outside what the budget reserved.
            response = self._client.with_options(max_retries=0).messages.create(
                max_tokens=max_tokens or self._config.max_tokens,
                thinking=THINKING,
                extra_headers={"anthropic-beta": BINDING_BETA},
                **self._prompt(system, tools, messages),
            )
        except anthropic.APIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        usage = response.usage
        details = response.stop_details
        return ModelReply(
            content=list(response.content),
            tool_calls=[
                ToolCall(block.id, block.name, dict(block.input))
                for block in response.content
                if block.type == "tool_use"
            ],
            text="".join(b.text for b in response.content if b.type == "text"),
            stop_reason=response.stop_reason or "",
            dropped_thinking=len(
                getattr(response, "input_transformations", None) or []
            ),
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
            refusal_category=getattr(details, "category", None),
        )
