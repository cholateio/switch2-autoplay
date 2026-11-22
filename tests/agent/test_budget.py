import pytest

from autoplay.core.agent.budget import (
    PRICES,
    Budget,
    BudgetExceeded,
    Usage,
    cost_usd,
)

OPUS = "claude-opus-5-5"


def test_input_and_output_price():
    assert cost_usd(OPUS, Usage(1_000_000, 100_000, 0, 0)) == pytest.approx(6.0)
    assert cost_usd("claude-sonnet-5-5", Usage(1_000_000, 100_000)) == pytest.approx(
        3.0
    )


def test_cache_prices():
    assert cost_usd(OPUS, Usage(0, 0, 1_000_000, 1_000_000)) == pytest.approx(5.20)
    assert cost_usd("claude-sonnet-5-5", Usage(0, 0, 1_000_000, 1_000_000)) == (
        pytest.approx(2.60)
    )


def test_unknown_model_has_no_price():
    with pytest.raises(ValueError, match="claude-opus-5-5"):
        cost_usd("gpt-x", Usage())
    with pytest.raises(ValueError):
        Budget("gpt-x", 2.0, 10, 8000)


def spend(budget: Budget, usd: float) -> None:
    budget.add(Usage(output_tokens=round(usd / PRICES[OPUS].output * 1_000_000)))


def test_reserve_refuses_when_a_full_reply_would_not_fit():
    budget = Budget(OPUS, max_usd=2.0, max_calls=100, max_tokens=8000)
    spend(budget, 1.0)
    budget.reserve(0)
    spend(budget, 0.9)
    assert budget.worst_case_usd(0) == pytest.approx(0.16)
    with pytest.raises(BudgetExceeded, match="2.00"):
        budget.reserve(0)


def test_reserve_prices_the_whole_prompt_at_the_dearest_input_rate():
    budget = Budget(OPUS, max_usd=0.20, max_calls=100, max_tokens=8000)
    assert budget.worst_case_usd(100_000) == pytest.approx(0.16 + 0.1 * 1.05 * 5.0)
    # The first call of a run is bounded too: a large prompt is refused.
    with pytest.raises(BudgetExceeded):
        budget.reserve(100_000)
    budget.reserve(1000)


def test_call_limit():
    budget = Budget(OPUS, max_usd=2.0, max_calls=2, max_tokens=8000)
    for _ in range(2):
        budget.reserve(10)
        budget.add(Usage(10, 10))
    assert budget.calls == 2
    with pytest.raises(BudgetExceeded, match="2 model calls"):
        budget.reserve(10)


def test_add_returns_the_cost_of_that_call():
    budget = Budget(OPUS, 2.0, 10, 8000)
    assert budget.add(Usage(1000, 1000)) == pytest.approx(0.024)
    assert budget.spent_usd == pytest.approx(0.024)


@pytest.mark.parametrize("growth", [1.0, 1.5, 4.0])
@pytest.mark.parametrize("billed_as", ["input_tokens", "cache_write_tokens"])
def test_spending_never_passes_the_limit(growth, billed_as):
    """Whatever the prompt grows to and however it is billed, as long as the
    billed prompt is the counted one and the reply fits max_tokens."""
    budget = Budget(OPUS, max_usd=0.5, max_calls=10_000, max_tokens=8000)
    prompt = 50_000
    while True:
        try:
            budget.reserve(prompt)
        except BudgetExceeded:
            break
        budget.add(Usage(output_tokens=8000, **{billed_as: prompt}))
        assert budget.spent_usd <= budget.max_usd
        prompt = int(prompt * growth)
    assert budget.spent_usd <= budget.max_usd


def test_the_reply_reserve_follows_the_replies_seen():
    budget = Budget("claude-opus-5-5", 1.0, 100, 3000)
    assert budget.reply_limit() == 3000
    budget.add(Usage(1000, 200, 0, 0))
    assert budget.reply_limit() == 1500
    budget.add(Usage(1000, 500, 0, 0))
    assert budget.reply_limit() == 2000
    budget.add(Usage(1000, 2900, 0, 0))
    assert budget.reply_limit() == 3000


def test_a_run_of_small_replies_gets_close_to_its_limit_and_never_past_it():
    # Token counts of the size an M4 run has with the history trimmed: about
    # 12 000 prompt tokens, mostly read from the cache, and replies of 300.
    budget = Budget("claude-opus-5-5", 1.0, 10_000, 3000)
    usage = Usage(input_tokens=1500, output_tokens=300, cache_read_tokens=10_500)
    while True:
        try:
            budget.reserve(12_000)
        except BudgetExceeded:
            break
        # The worst the call could have cost, had nothing come from the cache
        # and the reply run to its limit, still fits.
        worst = Usage(0, budget.reply_limit(), 0, int(12_000 * 1.05))
        assert budget.spent_usd + cost_usd(budget.model, worst) <= 1.0
        budget.add(usage)
    assert 0.90 <= budget.spent_usd <= 1.0


def test_a_prompt_billed_far_above_its_count_shows_as_an_overrun():
    budget = Budget("claude-opus-5-5", 0.10, 100, 1000)
    budget.reserve(1000)
    assert budget.overrun_usd == 0.0
    # Counted at 1000 tokens, billed at 40 000: the margin did not hold.
    budget.add(Usage(input_tokens=40_000, output_tokens=100))
    assert budget.overrun_usd == pytest.approx(0.162 - 0.10)
    with pytest.raises(BudgetExceeded):
        budget.reserve(1000)
