import ollama
from config import OLLAMA_MODEL, OLLAMA_NUM_CTX

class AnalystAgent:
    """
    Bull vs Bear debate system.
    Two personas argue opposing sides, then a Judge renders the final verdict.
    """
    def __init__(self):
        self.model = OLLAMA_MODEL

    # ═══════════════════════════════════════════════════════════════════
    # THE BULL 🐂
    # ═══════════════════════════════════════════════════════════════════

    BULL_SYSTEM = """You are THE BULL 🐂 — an aggressive, conviction-driven market optimist.

Your personality:
- You see opportunity everywhere. Every dip is a buying opportunity.
- You focus on growth catalysts, momentum, institutional accumulation, and macro tailwinds.
- You dismiss bear arguments as fear-mongering and "priced in."
- You use confident, punchy language. You talk like a trader who's up 400% YTD.
- You cite specific bullish data: revenue growth, TAM expansion, insider buying, short squeeze potential.
- You never hedge or qualify — you are ALL IN on the bull case.

Rules:
- Make your STRONGEST case for buying / holding.
- Cite specific data points from the market intelligence provided.
- Attack the bear case directly — explain why bearish arguments are wrong.
- Keep it under 800 characters. Be punchy, not verbose."""

    BEAR_SYSTEM = """You are THE BEAR 🐻 — a ruthless, data-driven market skeptic.

Your personality:
- You see risk everywhere. Every rally is a trap, every earnings beat is a sell-the-news event.
- You focus on overvaluation, insider selling, deteriorating fundamentals, macro headwinds, and credit risk.
- You dismiss bull arguments as copium and "greater fool" delusion.
- You use sharp, surgical language. You talk like a short seller who called the 2008 crash.
- You cite specific bearish data: P/E compression, margin degradation, debt ratios, sector rotation.
- You never concede ground — every silver lining has a dark cloud.

Rules:
- Make your STRONGEST case for selling / avoiding.
- Cite specific data points from the market intelligence provided.
- Attack the bull case directly — explain why bullish arguments are delusional.
- Keep it under 800 characters. Be sharp, not whiny."""

    JUDGE_SYSTEM = """You are THE JUDGE ⚖️ — a cold, impartial market arbiter.

Your personality:
- You weigh both arguments purely on evidence quality, not emotional conviction.
- You identify which side has stronger data backing their claims.
- You're not afraid to call out weak arguments from either side.
- You give a final VERDICT with a confidence score.

Rules:
1. Evaluate the Bull's strongest point and the Bear's strongest point.
2. Identify which argument has more data support.
3. Call out any logical fallacies or emotional reasoning from either side.
4. Render your VERDICT in this exact format:

VERDICT: [BUY / SELL / HOLD]
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
        Run a full bull vs bear debate on a topic.
        Returns dict with bull_case, bear_case, and verdict.
        """
        context = f"Topic: {topic}"
        if market_data:
            context += f"\n\nMARKET DATA:\n{market_data}"

        # Round 1: Bull makes the case
        bull_prompt = f"Make your STRONGEST bull case.\n\n{context}"
        bull_case = self._call_llm(self.BULL_SYSTEM, bull_prompt)

        # Round 2: Bear responds and attacks
        bear_prompt = f"The Bull just argued:\n\"{bull_case}\"\n\nDestroy their argument and make your bear case.\n\n{context}"
        bear_case = self._call_llm(self.BEAR_SYSTEM, bear_prompt)

        # Round 3: Judge weighs in
        judge_prompt = f"""Two analysts debated {topic}:

🐂 BULL argued:
"{bull_case}"

🐻 BEAR argued:
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
        output = f"🐂 **THE BULL:**\n{result['bull_case']}\n\n"
        output += f"🐻 **THE BEAR:**\n{result['bear_case']}\n\n"
        output += f"⚖️ **THE VERDICT:**\n{result['verdict']}"
        return output

    # ═══════════════════════════════════════════════════════════════════
    # CONSENSUS REPORT (used by !report)
    # ═══════════════════════════════════════════════════════════════════

    def generate_consensus(self, news_list, social_list):
        """Generate a market consensus using bull/bear debate on the data."""
        news_list = news_list or []
        social_list = social_list or []

        print(f"[AnalystAgent] Debating consensus from {len(news_list)} news + {len(social_list)} social items")

        combined_data = "NEWS:\n" + "\n".join([f"- {n}" for n in news_list[:15]])
        combined_data += "\n\nSOCIAL:\n" + "\n".join([f"- {s}" for s in social_list[:15]])

        # Bull argues the data is bullish
        bull_prompt = f"Based on today's market intelligence, make your case that the overall market is BULLISH. Be specific — cite headlines, social signals, and data points.\n\nDATA:\n{combined_data}"
        bull_case = self._call_llm(self.BULL_SYSTEM, bull_prompt)

        # Bear argues the data is bearish
        bear_prompt = f"The Bull just argued:\n\"{bull_case}\"\n\nBased on today's market intelligence, make your case that the market is BEARISH. Attack the bull's points and cite evidence.\n\nDATA:\n{combined_data}"
        bear_case = self._call_llm(self.BEAR_SYSTEM, bear_prompt)

        # Judge renders overall market verdict
        judge_prompt = f"""Two analysts debated today's market outlook:

🐂 BULL argued:
"{bull_case}"

🐻 BEAR argued:
"{bear_case}"

MARKET DATA:
{combined_data}

Render your verdict on the OVERALL MARKET DIRECTION."""
        verdict = self._call_llm(self.JUDGE_SYSTEM, judge_prompt)

        report = f"# 🐂 vs 🐻 MARKET DEBATE\n\n"
        report += f"## 🐂 THE BULL SAYS:\n{bull_case}\n\n"
        report += f"## 🐻 THE BEAR SAYS:\n{bear_case}\n\n"
        report += f"## ⚖️ THE VERDICT:\n{verdict}"

        return report

    # ═══════════════════════════════════════════════════════════════════
    # CHAT WITH MEMORY (conversational mode)
    # ═══════════════════════════════════════════════════════════════════

    def chat_with_memory(self, user_message, history, live_context=None):
        """Conversational chat — uses MarketMind persona for general questions."""
        system_prompt = """You are MarketMind, the moderator of a financial debate show between The Bull 🐂 and The Bear 🐻.

Your capabilities:
- Answer questions about stocks, commodities, options, and market dynamics
- When asked about a specific ticker or trade idea, briefly present both bull and bear perspectives
- Explain trading concepts, strategies, and market mechanics
- Discuss market rumors, sentiment, and speculation openly

Your personality:
- Balanced but sharp — you respect both sides of every trade
- Financially savvy and slightly cynical about market narratives
- When the user asks "should I buy X", give both the bull and bear case briefly
- Direct and to the point — no disclaimers, no hedging

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
