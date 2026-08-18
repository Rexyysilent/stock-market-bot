"""Static safeguards for the public repository's neutral tooling scope."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


readme = read("README.md")
for required in (
    "# Public Market Data & Observability Bot",
    "An open-source, local-first data collection and observability pipeline",
    "does not provide paid or free trading signals",
    "not legal advice or a representation",
    "Optional commercial data providers remain replaceable adapters",
    "does not operate a hosted recommendation feed or subscriber tier",
):
    assert required in readme, required

publishing = read("docs/PUBLISHING_CHECKLIST.md")
for required in (
    "does not advertise paid or free signal tiers",
    "do not require a commercial market-data subscription",
    "not a claim of registration, exemption, or compliance",
):
    assert required in publishing, required

analyst = read("agents/analyst_agent.py")
for required in (
    "You are an impartial evidence synthesizer.",
    "EVIDENCE BALANCE:",
    "Do not provide buy/sell/hold recommendations",
    "decline the directional instruction",
):
    assert required in analyst, required

public_runtime = "\n".join(
    read(path)
    for path in (
        "agents/analyst_agent.py",
        "agents/research_agent.py",
        "agents/watcher_agent.py",
        "bot.py",
        "signals.py",
    )
)
for forbidden in (
    "THE VERDICT",
    "structural alpha",
    "dip signal",
    "potential reversal",
):
    assert forbidden not in public_runtime, forbidden

config = read("config.py")
assert "PAPER_PORTFOLIO" not in config

print("Public neutral-tooling positioning checks passed")
