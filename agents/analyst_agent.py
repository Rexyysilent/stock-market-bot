import ollama
from config import OLLAMA_MODEL, OLLAMA_NUM_CTX

class AnalystAgent:
    """
    Evidence review system.
    Two perspectives summarize supportive and cautionary evidence, then a judge weighs the result.
    """
    def __init__(self):
        self.model = OLLAMA_MODEL

    # ═══════════════════════════════════════════════════════════════════
    # CONSTRUCTIVE CASE
    # ═══════════════════════════════════════════════════════════════════

    BULL_SYSTEM = """You are the constructive analyst - focused on supportive market evidence.

Your personality:
- You focus on growth catalysts, momentum, institutional accumulation, and macro tailwinds.
- You cite specific supportive data: revenue growth, TAM expansion, insider activity, and positioning.
- You keep the tone evidence-first and avoid trade instructions.

Rules:
- Make the strongest constructive case.
- Cite specific data points from the market intelligence provided.
- Explain where the cautionary case may be less supported.
- Keep it under 800 characters. Be clear, not promotional."""

    BEAR_SYSTEM = """You are the cautionary analyst - focused on risk and downside evidence.

Your personality:
- You focus on overvaluation, insider selling, deteriorating fundamentals, macro headwinds, and credit risk.
- You cite specific cautionary data: P/E compression, margin pressure, debt ratios, sector rotation, and weak source coverage.
- You keep the tone evidence-first and avoid trade instructions.

Rules:
- Make the strongest cautionary case.
- Cite specific data points from the market intelligence provided.
- Explain where the constructive case may be less supported.
- Keep it under 800 characters. Be clear, not alarmist."""

    JUDGE_SYSTEM = """You are THE JUDGE ⚖️ — a cold, impartial market arbiter.

Your personality:
- You weigh both arguments purely on evidence quality, not emotional conviction.
- You identify which side has stronger data backing their claims.
- You're not afraid to call out weak arguments from either side.
- You give a final VERDICT with a confidence score.

Rules:
1. Evaluate the constructive strongest point and the cautionary strongest point.
2. Identify which argument has more data support.
3. Call out any logical fallacies or emotional reasoning from either side.
4. Render your VERDICT in this exact format:

VERDICT: [SUPPORTIVE / CAUTIOUS / MIXED]
CONFIDENCE: [1-10]
REASONING: [2-3 sentences explaining why one side won]
KEY RISK: [The single biggest risk to your verdict]

Keep it under 600 characters. Be decisive."""

    # ═══════════════════════════════════════════════════════════════════
    # CORE DEBATE ENGINE
    # ═══════════════════════════════════════════════════════════════════

    def _call_llm(self, system_prompt, user_prompt):
        """Send a message to Ollama with a specific persona."""
        try:
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ]
            response = ollama.chat(model=self.model, messages=messages, options={'num_ctx': OLLAMA_NUM_CTX})
            return response['message']['content']
        except Exception as e:
            return f"[LLM Error: {e}]"

    def debate(self, topic, market_data=""):
        """
        Run a full evidence review on a topic.
        Returns dict with constructive case, cautionary case, and verdict.
        """
        context = f"Topic: {topic}"
        if market_data:
            context += f"\n\nMARKET DATA:\n{market_data}"

        # Round 1: constructive case
        bull_prompt = f"Make the strongest constructive case.\n\n{context}"
        bull_case = self._call_llm(self.BULL_SYSTEM, bull_prompt)

        # Round 2: cautionary case
        bear_prompt = f"The constructive analyst argued:\n\"{bull_case}\"\n\nMake the cautionary case and cite evidence.\n\n{context}"
        bear_case = self._call_llm(self.BEAR_SYSTEM, bear_prompt)

        # Round 3: Judge weighs in
        judge_prompt = f"""Two analysts debated {topic}:

Constructive analyst argued:
"{bull_case}"

Cautionary analyst argued:
"{bear_case}"

MARKET DATA:
{market_data if market_data else 'No additional data provided.'}

Render your verdict."""
        verdict = self._call_llm(self.JUDGE_SYSTEM, judge_prompt)

        return {
            "topic": topic,
            "bull_case": bull_case,
            "bear_case": bear_case,
            "verdict": verdict
        }

    # ═══════════════════════════════════════════════════════════════════
    # SIMPLE SENTIMENT (backward compat)
    # ═══════════════════════════════════════════════════════════════════

    def analyze_sentiment(self, text):
        """Quick sentiment via debate format — returns formatted string."""
        result = self.debate(text)
        output = f"**Constructive Case:**\n{result['bull_case']}\n\n"
        output += f"**Cautionary Case:**\n{result['bear_case']}\n\n"
        output += f"**Verdict:**\n{result['verdict']}"
        return output

    # ═══════════════════════════════════════════════════════════════════
    # CONSENSUS REPORT (used by !report)
    # ═══════════════════════════════════════════════════════════════════

    def generate_consensus(self, news_list, social_list):
        """Generate a market consensus using constructive/cautionary evidence review."""
        news_list = news_list or []
        social_list = social_list or []

        print(f"[AnalystAgent] Debating consensus from {len(news_list)} news + {len(social_list)} social items")

        combined_data = "NEWS:\n" + "\n".join([f"- {n}" for n in news_list[:15]])
        combined_data += "\n\nSOCIAL:\n" + "\n".join([f"- {s}" for s in social_list[:15]])

        # Constructive case
        bull_prompt = f"Based on today's market intelligence, make the constructive case for the overall market. Be specific - cite headlines, social indicators, and data points.\n\nDATA:\n{combined_data}"
        bull_case = self._call_llm(self.BULL_SYSTEM, bull_prompt)

        # Cautionary case
        bear_prompt = f"The constructive analyst argued:\n\"{bull_case}\"\n\nBased on today's market intelligence, make the cautionary case for the overall market and cite evidence.\n\nDATA:\n{combined_data}"
        bear_case = self._call_llm(self.BEAR_SYSTEM, bear_prompt)

        # Judge renders overall market verdict
        judge_prompt = f"""Two analysts debated today's market outlook:

Constructive analyst argued:
"{bull_case}"

Cautionary analyst argued:
"{bear_case}"

MARKET DATA:
{combined_data}

Render your verdict on the OVERALL MARKET DIRECTION."""
        verdict = self._call_llm(self.JUDGE_SYSTEM, judge_prompt)

        report = f"# MARKET EVIDENCE REVIEW\n\n"
        report += f"## Constructive Case:\n{bull_case}\n\n"
        report += f"## Cautionary Case:\n{bear_case}\n\n"
        report += f"## Verdict:\n{verdict}"

        return report

    # ═══════════════════════════════════════════════════════════════════
    # CHAT WITH MEMORY (conversational mode)
    # ═══════════════════════════════════════════════════════════════════

    def chat_with_memory(self, user_message, history, live_context=None):
        """Conversational chat — uses MarketMind persona for general questions."""
        system_prompt = """You are MarketMind, a balanced market research assistant.

Your capabilities:
- Answer questions about stocks, commodities, options, and market dynamics
- When asked about a specific ticker or trade idea, briefly present both constructive and cautionary perspectives
- Explain trading concepts, strategies, and market mechanics
- Discuss market rumors, sentiment, and speculation openly

Your personality:
- Balanced but sharp — you respect both sides of every trade
- Financially savvy and slightly cynical about market narratives
- When the user asks about a possible position, give both constructive and cautionary context briefly
- Direct and to the point while avoiding trade instructions

Keep responses under 1800 characters. Be helpful and informative."""

        if live_context:
            system_prompt += f"""

CURRENT MARKET INTELLIGENCE (just gathered):
{live_context}

Use this data to inform your answers. Cite sources like [r/wallstreetbets] or [ZeroHedge] when referencing this data."""

        messages = [{'role': 'system', 'content': system_prompt}]
        messages.extend(history)
        messages.append({'role': 'user', 'content': user_message})

        try:
            response = ollama.chat(model=self.model, messages=messages, options={'num_ctx': OLLAMA_NUM_CTX})
            return response['message']['content']
        except Exception as e:
            return f"I lost my train of thought... ({e})"
