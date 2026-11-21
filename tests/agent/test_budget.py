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
    budget.reserve()
    spend(budget, 0.9)
    assert budget.worst_case_usd() == pytest.approx(0.16)
    with pytest.raises(BudgetExceeded, match="2.00"):
        budget.reserve()


def test_reserve_counts_the_last_input_grown():
    budget = Budget(OPUS, max_usd=1.0, max_calls=100, max_tokens=8000)
    budget.add(Usage(input_tokens=100_000))
    assert budget.worst_case_usd() == pytest.approx(0.16 + 0.4 * 1.5)
    with pytest.raises(BudgetExceeded):
        budget.reserve()


def test_call_limit():
    budget = Budget(OPUS, max_usd=2.0, max_calls=2, max_tokens=8000)
    for _ in range(2):
        budget.reserve()
        budget.add(Usage(10, 10))
    assert budget.calls == 2
    with pytest.raises(BudgetExceeded, match="2 model calls"):
        budget.reserve()


def test_add_returns_the_cost_of_that_call():
    budget = Budget(OPUS, 2.0, 10, 8000)
    assert budget.add(Usage(1000, 1000)) == pytest.approx(0.024)
    assert budget.spent_usd == pytest.approx(0.024)


@pytest.mark.parametrize("growth", [1.0, 1.2, 1.5])
@pytest.mark.parametrize("output_tokens", [100, 4000, 8000])
def test_spending_never_passes_the_limit_while_calls_stay_within_the_reserve(
    growth, output_tokens
):
    budget = Budget(OPUS, max_usd=0.5, max_calls=10_000, max_tokens=8000)
    cache_read = 0
    budget.reserve()
    budget.add(Usage(output_tokens=output_tokens))
    while True:
        try:
            budget.reserve()
        except BudgetExceeded:
            break
        cache_read = max(2000, int(cache_read * growth))
        budget.add(Usage(cache_read_tokens=cache_read, output_tokens=output_tokens))
        assert budget.spent_usd <= budget.max_usd
    assert budget.calls > 1
