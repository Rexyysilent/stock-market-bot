"""Guard user-visible generated prose while permitting schema identifiers.

This intentionally checks authored prompts and display strings, not acquired
headlines or other source quotations that can contain directional language.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


analyst = read("agents/analyst_agent.py")
for required in (
    "You are an impartial evidence synthesizer.",
    "EVIDENCE BALANCE:",
    "Do not provide buy/sell/hold recommendations",
    "decline the directional instruction",
    "Evidence Balance",
):
    assert required in analyst, required

runtime = "\n".join(
    read(path)
    for path in (
        "agents/analyst_agent.py",
        "agents/research_agent.py",
        "bot.py",
        "signals.py",
    )
)
for retired_authored_phrase in (
    "THE VERDICT",
    "structural alpha",
    "potential reversal",
    "Sniper-layer signal derivations",
    "CEO.CA / URANIUM INTELLIGENCE",
):
    assert retired_authored_phrase not in runtime, retired_authored_phrase

# Compatibility identifiers remain valid and are not treated as public claims.
assert '"verdict": verdict' in analyst
assert "Legacy public keys containing ``signal`` or ``alert``" in runtime
bot = read("bot.py")
assert "format_percent(rotation.get('growth_5d'))" in bot
assert "format_percent(rotation.get('spread_5d')).replace('%', ' percentage points')" in bot

print("Public neutral-research runtime wording checks passed")
