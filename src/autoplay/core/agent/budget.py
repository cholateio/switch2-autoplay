from dataclasses import dataclass

from autoplay.core import clock


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# Checked against the published prices on 2026-11-22. `cache_write` is the
# 5-minute rate, the only one the loop uses.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.10, 2.5),
}

# The counted prompt size is an estimate; reserve this much more.
COUNT_MARGIN = 1.05
# After the first call a reply may be this many times the longest one so far,
# and never less than the floor: room for a turn that thinks longer.
REPLY_GROWTH = 4
REPLY_FLOOR = 1500
# A cache entry lives 5 minutes from its last use; past this a read is not assumed.
CACHE_TRUST_S = 240.0


@dataclass(frozen=True)
class Usage:
    """What one model call was billed for. `input_tokens` is the part of the
    prompt neither read from nor written to the cache."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _price(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise ValueError(
            f"no price is known for model {model!r}; known models: {', '.join(PRICES)}"
        ) from None


def cost_usd(model: str, usage: Usage) -> float:
    """Raises ValueError for a model with no known price."""
    price = _price(model)
    return (
        usage.input_tokens * price.input
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
        + usage.output_tokens * price.output
    ) / 1_000_000


class BudgetExceeded(RuntimeError):
    """The next model call could take the run past one of its limits."""


class Budget:
    """The spending limits of one run. `reserve()` goes before every model
    call and `add()` after it. A call is only made when its worst case fits:
    the prompt plus a reply of `reply_limit()` tokens. The part of the prompt
    that the call before read from the cache is priced as a cache read, as
    long as that call is recent and `history_edited()` was not called since;
    the rest is priced at the dearest input rate. The caller must send the call with that limit as
    its `max_tokens`. Then `spent_usd` does not pass `max_usd` as long as the
    prompt is billed at no more than COUNT_MARGIN times its counted size and
    the prefix read from the cache last time is read from it again. Neither
    is enforced by the API: the count is its own estimate, and a cache entry
    can be missed. `overrun_usd`
    says by how much the limit was passed if it ever was."""

    def __init__(
        self, model: str, max_usd: float, max_calls: int, max_tokens: int
    ) -> None:
        self._price = _price(model)
        self.model = model
        self.max_usd = max_usd
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.spent_usd = 0.0
        self.calls = 0
        self._longest_reply = 0
        self._cached_tokens = 0
        self._cached_at = 0.0

    def history_edited(self) -> None:
        """Tell the budget that earlier turns were changed: what was cached of
        them will not be read again."""
        self._cached_tokens = 0

    def reply_limit(self) -> int:
        """The `max_tokens` the next call is to be sent with: a full-length
        reply for the first call, afterwards bounded by the replies seen."""
        if self.calls == 0:
            return self.max_tokens
        return min(
            self.max_tokens, max(REPLY_FLOOR, REPLY_GROWTH * self._longest_reply)
        )

    @property
    def overrun_usd(self) -> float:
        return max(0.0, self.spent_usd - self.max_usd)

    def worst_case_usd(self, input_tokens: int) -> float:
        """The most a call with a prompt counted at `input_tokens` can cost."""
        dearest = max(
            self._price.input, self._price.cache_read, self._price.cache_write
        )
        cached = 0
        if clock.now() - self._cached_at <= CACHE_TRUST_S:
            cached = min(self._cached_tokens, input_tokens)
        return (
            cached * self._price.cache_read
            + (input_tokens * COUNT_MARGIN - cached) * dearest
            + self.reply_limit() * self._price.output
        ) / 1_000_000

    def reserve(self, input_tokens: int) -> None:
        """Raise BudgetExceeded, naming the limit, unless a call whose prompt
        was counted at `input_tokens` fits."""
        if self.calls >= self.max_calls:
            raise BudgetExceeded(
                f"the limit of {self.max_calls} model calls is reached"
            )
        worst = self.worst_case_usd(input_tokens)
        if self.spent_usd + worst > self.max_usd:
            raise BudgetExceeded(
                f"US${self.spent_usd:.4f} spent; one more call could cost "
                f"US${worst:.4f}, past the limit of US${self.max_usd:.2f}"
            )

    def add(self, usage: Usage) -> float:
        """Record one finished call and return what it cost in US dollars."""
        cost = cost_usd(self.model, usage)
        self.spent_usd += cost
        self.calls += 1
        self._longest_reply = max(self._longest_reply, usage.output_tokens)
        # Only what was read: a part written this time has not yet proved it
        # can be read back.
        self._cached_tokens = usage.cache_read_tokens
        self._cached_at = clock.now()
        return cost
