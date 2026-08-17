import asyncio
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from config import (
    DISCORD_HISTORY_ENTRIES_PER_CHANNEL,
    DISCORD_MAX_HISTORY_CHANNELS,
    DISCORD_MAX_TRACKED_USERS,
    DISCORD_REQUEST_COOLDOWN_SECONDS,
    GUILD_ID,
)
from discord_guard import BoundedChatHistory, RequestGate
from agents.news_agent import NewsAgent
from agents.social_agent import SocialAgent
from agents.analyst_agent import AnalystAgent
from agents.watcher_agent import WatcherAgent
from agents.research_agent import ResearchAgent
from agents.twitter_agent import TwitterAgent
from agents.sec_agent import SECAgent
from database import log_sentiment, export_for_notebooklm, init_db

load_dotenv()

# Initialize Database on startup
init_db()

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize Agents
news_agent = NewsAgent()
social_agent = SocialAgent()
analyst_agent = AnalystAgent()
watcher_agent = WatcherAgent()
research_agent = ResearchAgent()
twitter_agent = TwitterAgent()
sec_agent = SECAgent()

chat_history = BoundedChatHistory(
    max_channels=DISCORD_MAX_HISTORY_CHANNELS,
    max_entries_per_channel=DISCORD_HISTORY_ENTRIES_PER_CHANNEL,
)
request_gate = RequestGate(DISCORD_REQUEST_COOLDOWN_SECONDS, max_users=DISCORD_MAX_TRACKED_USERS)
bot_work_lock = asyncio.Lock()

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord and the Council is ready.')

def _collect_live_context():
    """Run blocking provider collection away from Discord's event loop."""
    whispers = social_agent.get_whisper()
    whisper_context = "\n".join(whispers[:15]) if whispers else ""
    ceo_signals = research_agent.get_ceo_ca_signals()
    trials = research_agent.get_clinical_trials()

    research_context = "\n--- URANIUM/CEO.CA INTEL ---\n"
    for signal in ceo_signals[:8]:
        research_context += f"[{signal['ticker']}] {signal['title'][:100]}\n"
    research_context += "\n--- CLINICAL TRIALS (CRISPR/VERTEX) ---\n"
    for trial in trials[:5]:
        research_context += (
            f"[{trial['nct_id']}] {trial['title'][:80]} - {trial['status']}\n"
        )

    twitter_data = twitter_agent.get_twitter_intel(max_queries=3)
    research_context += "\n--- X/TWITTER NARRATIVE INDICATORS ---\n"
    for item in twitter_data[:5]:
        research_context += f"[X/{item['query']}] {item['title'][:80]}\n"
    return (
        whisper_context + "\n" + research_context
        if whisper_context else research_context
    )


async def _handle_mention(message):
    user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
    if not user_text:
        return

    channel_id = message.channel.id
    history = chat_history.recent(channel_id, 10)
    async with message.channel.typing():
        await message.channel.send(
            "*Gathering market intelligence from all sources...*"
        )
        live_context = await asyncio.to_thread(_collect_live_context)
        response = await asyncio.to_thread(
            analyst_agent.chat_with_memory,
            user_text,
            history,
            live_context,
        )

    chat_history.append(channel_id, 'user', user_text)
    chat_history.append(channel_id, 'assistant', response)
    await message.channel.send(response)


@bot.event
async def on_message(message):
    if message.author == bot.user or getattr(message.author, "bot", False):
        return

    is_mention = bot.user.mentioned_in(message)
    is_command = message.content.startswith('!')
    if not is_mention and not is_command:
        return

    if message.guild is None or GUILD_ID is None or message.guild.id != GUILD_ID:
        return

    allowed, retry_after = request_gate.allow(message.author.id)
    if not allowed:
        await message.channel.send(
            f"Please wait {max(1, int(retry_after + 0.999))}s before another request."
        )
        return
    if bot_work_lock.locked():
        await message.channel.send("The bot is busy with another request. Try again shortly.")
        return

    async with bot_work_lock:
        if is_mention:
            await _handle_mention(message)
        if is_command:
            await bot.process_commands(message)

@bot.command(name='report')
async def daily_report(ctx):
    """Generates a FULL raw data market report - no summarization, all details."""
    await ctx.send("🔮 **FULL INTELLIGENCE DUMP** - Gathering ALL data from sub-agents...")

    # 1. Gather ALL Data
    headlines, whispers, prices = await asyncio.gather(
        asyncio.to_thread(news_agent.get_global_headlines),
        asyncio.to_thread(social_agent.get_whisper),
        asyncio.to_thread(watcher_agent.get_full_report),
    )
    whispers = whispers or []

    print(f"[Report] Headlines: {len(headlines)}, Whispers: {len(whispers)}, Prices: {len(prices)}")

    # ═══════════════════════════════════════════════════════════════════
    # EMBED 1: STOCK PRICES (Individual stocks from watchlist)
    # ═══════════════════════════════════════════════════════════════════
    from config import WATCHLIST_STOCKS, WATCHLIST_COMMODITIES

    stock_lines = []
    for ticker in WATCHLIST_STOCKS:
        if ticker in prices:
            stock_lines.append(f"**{ticker}**: {prices[ticker]}")

    embed_stocks = discord.Embed(title="📈 STOCK WATCHLIST", color=0x2ecc71)
    embed_stocks.add_field(name="Individual Stocks", value="\n".join(stock_lines) or "N/A", inline=False)
    await ctx.send(embed=embed_stocks)

    # ═══════════════════════════════════════════════════════════════════
    # EMBED 2: COMMODITIES / ETFs
    # ═══════════════════════════════════════════════════════════════════
    commodity_lines = []
    for ticker in WATCHLIST_COMMODITIES:
        if ticker in prices:
            commodity_lines.append(f"**{ticker}**: {prices[ticker]}")

    embed_commodities = discord.Embed(title="🥇 COMMODITIES & ETFs", color=0xf39c12)
    embed_commodities.add_field(name="Precious Metals / Energy / Uranium", value="\n".join(commodity_lines) or "N/A", inline=False)
    await ctx.send(embed=embed_commodities)

    # ═══════════════════════════════════════════════════════════════════
    # EMBED 3: NEWS HEADLINES (with full links)
    # ═══════════════════════════════════════════════════════════════════
    embed_news = discord.Embed(title="📰 NEWS HEADLINES", color=0x3498db)
    for i, h in enumerate(headlines[:5], 1):
        # Headlines come as "Title (link)" - split them
        if "(" in h and h.endswith(")"):
            title = h[:h.rfind("(")].strip()
            link = h[h.rfind("(")+1:-1]
            embed_news.add_field(name=f"{i}. {title[:200]}", value=f"[Source]({link})", inline=False)
        else:
            embed_news.add_field(name=f"{i}. Headline", value=h[:500], inline=False)
    await ctx.send(embed=embed_news)

    # ═══════════════════════════════════════════════════════════════════
    # EMBEDS 4-6: TOP SOCIAL WHISPERS (ranked by engagement)
    # ═══════════════════════════════════════════════════════════════════
    # Sort whispers by score (extract from "Score: X" pattern)
    def get_score(w):
        try:
            if "Score:" in w:
                score_part = w.split("Score:")[1].split("|")[0].strip()
                return int(score_part)
        except:
            pass
        return 0

    sorted_whispers = sorted(whispers, key=get_score, reverse=True)

    # Split into chunks for Discord (max 1024 chars per field)
    whisper_chunks = []
    current_chunk = []
    current_len = 0

    for w in sorted_whispers[:30]:  # Top 30 by engagement
        line = f"• {w[:150]}"
        if current_len + len(line) + 1 > 1000:
            whisper_chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1

    if current_chunk:
        whisper_chunks.append("\n".join(current_chunk))

    # Send whisper embeds
    colors = [0xe74c3c, 0x9b59b6, 0x1abc9c]  # Red, Purple, Teal
    for i, chunk in enumerate(whisper_chunks[:3]):  # Max 3 embeds for whispers
        embed_whisper = discord.Embed(
            title=f"👂 SOCIAL ITEMS (Part {i+1}/{min(len(whisper_chunks), 3)})",
            color=colors[i % len(colors)]
        )
        embed_whisper.add_field(name="Top Whispers by Engagement", value=chunk, inline=False)
        await ctx.send(embed=embed_whisper)

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY STATS
    # ═══════════════════════════════════════════════════════════════════
    embed_summary = discord.Embed(title="📊 INTELLIGENCE SUMMARY", color=0x95a5a6)
    embed_summary.add_field(name="Data Collected", value=f"• {len(headlines)} headlines\n• {len(whispers)} social items\n• {len(prices)} price points", inline=True)
    embed_summary.add_field(name="Commands", value="`!whisper` - More items\n`!dump` - Export daily brief\n`!analyze <ticker>` - Deep dive", inline=True)
    embed_summary.set_footer(text="Raw data dump complete. Use !dump to export for deeper AI analysis.")
    await ctx.send(embed=embed_summary)

@bot.command(name='analyze')
async def analyze_ticker(ctx, query: str):
    """Quick constructive/cautionary evidence review for a topic or ticker."""
    await ctx.send(f"Reviewing evidence for **{query}**...")

    sentiment = await asyncio.to_thread(
        analyst_agent.analyze_sentiment, f"Quick take on {query}"
    )

    # Split into chunks if too long for Discord
    if len(sentiment) > 1900:
        parts = [sentiment[i:i+1900] for i in range(0, len(sentiment), 1900)]
        for part in parts:
            await ctx.send(part)
    else:
        await ctx.send(sentiment)

@bot.command(name='debate')
async def full_debate(ctx, ticker: str):
    """Full constructive/cautionary review with live market data."""
    ticker = ticker.upper()
    await ctx.send(f"⚖️ **EVIDENCE REVIEW: {ticker}** — Gathering live data...")

    # Gather live market context
    market_data_lines = []

    # Price
    price_str = await asyncio.to_thread(watcher_agent.get_stock_price, ticker)
    if price_str:
        market_data_lines.append(f"Current Price: {price_str}")

    # Technicals
    tech = await asyncio.to_thread(
        watcher_agent.check_technical_indicators, ticker
    )
    if not tech.get("error"):
        market_data_lines.append(f"RSI: {tech.get('rsi', 'N/A')}")
        market_data_lines.append(f"Trend: {tech.get('trend', 'N/A')}")
        if tech.get('rsi_divergence'):
            market_data_lines.append(f"RSI Divergence: {tech['rsi_divergence']}")
        if tech.get('pct_from_52w_high'):
            market_data_lines.append(f"From 52w High: {tech['pct_from_52w_high']}%")
        if tech.get('volume_ratio'):
            market_data_lines.append(f"Volume vs avg: {tech['volume_ratio']}x")
        if tech.get('alerts'):
            market_data_lines.append("Notes: " + " | ".join(tech['alerts']))

    # Options flow
    options = await asyncio.to_thread(watcher_agent.get_options_flow, ticker)
    if not options.get("error"):
        market_data_lines.append(f"Options P/C Vol Ratio: {options.get('put_call_vol_ratio', 'N/A')}")
        market_data_lines.append(f"Options P/C OI Ratio: {options.get('put_call_oi_ratio', 'N/A')}")
        if options.get('alerts'):
            market_data_lines.append("Options notes: " + " | ".join(options['alerts']))

    market_data = "\n".join(market_data_lines) if market_data_lines else "No live data available."

    await ctx.send(f"📊 Data gathered. Reviewing both perspectives...")

    # Run the evidence review
    result = await asyncio.to_thread(analyst_agent.debate, ticker, market_data)

    # Constructive-case embed
    embed_bull = discord.Embed(
        title=f"CONSTRUCTIVE CASE — {ticker}",
        description=result['bull_case'][:4000],
        color=0x27ae60  # Green
    )
    await ctx.send(embed=embed_bull)

    # Cautionary-case embed
    embed_bear = discord.Embed(
        title=f"CAUTIONARY CASE — {ticker}",
        description=result['bear_case'][:4000],
        color=0xe74c3c  # Red
    )
    await ctx.send(embed=embed_bear)

    # Verdict embed
    embed_verdict = discord.Embed(
        title=f"⚖️ THE VERDICT on {ticker}",
        description=result['verdict'][:4000],
        color=0xf39c12  # Gold
    )
    embed_verdict.set_footer(text=f"Market data: {price_str or 'N/A'} | RSI: {tech.get('rsi', 'N/A')} | Model: {analyst_agent.model}")
    await ctx.send(embed=embed_verdict)

@bot.command(name='whisper')
async def show_whispers(ctx):
    """Shows raw social chatter."""
    whispers = await asyncio.to_thread(social_agent.get_whisper)
    await ctx.send("**Latest Whispers:**\n" + "\n".join(whispers[:5]))

@bot.command(name='dump')
async def dump_data(ctx):
    """Exports gathered intelligence to a local data file."""
    await ctx.send("Building the local data export...")
    filename = await asyncio.to_thread(export_for_notebooklm)
    await ctx.send(file=discord.File(filename))
    await ctx.send(
        "Raw data export complete. Review source health and timestamps before analysis."
    )

@bot.command(name='research')
async def research_dump(ctx):
    """Shows CEO.ca uranium context and ClinicalTrials.gov data."""
    await ctx.send("🔬 **RESEARCH AGENT** - Fetching specialized intelligence...")

    # CEO.ca / Uranium
    await ctx.send("⛏️ Fetching CEO.ca / Uranium context (UUUU, CCJ, NXE, DNN)...")
    ceo_signals = await asyncio.to_thread(research_agent.get_ceo_ca_signals)

    embed_ceo = discord.Embed(title="⛏️ CEO.CA / URANIUM INTELLIGENCE", color=0x2ecc71)
    embed_ceo.set_footer(text="Focus: Core samples, geology maps, permit delays")

    if ceo_signals:
        ceo_text = ""
        for s in ceo_signals[:10]:
            line = f"**[{s['ticker']}]** {s['title'][:80]}...\n"
            if len(ceo_text) + len(line) < 1000:
                ceo_text += line
        embed_ceo.add_field(name="Latest Items", value=ceo_text or "No items found", inline=False)
    else:
        embed_ceo.add_field(name="Status", value="No items found", inline=False)

    await ctx.send(embed=embed_ceo)

    # Clinical Trials
    await ctx.send("💉 Fetching ClinicalTrials.gov (CRISPR, Vertex, Gene Editing)...")
    trials = await asyncio.to_thread(research_agent.get_clinical_trials)

    embed_trials = discord.Embed(title="💉 CLINICALTRIALS.GOV / CRISPR", color=0x9b59b6)
    embed_trials.set_footer(text="Focus: Vertex, CRISPR Therapeutics, Gene Editing")

    if trials:
        for t in trials[:5]:  # Top 5 trials
            status_emoji = "🟢" if t['status'] == "RECRUITING" else "🟡" if "ACTIVE" in t['status'] else "⚪"
            embed_trials.add_field(
                name=f"{status_emoji} {t['nct_id']} - {t['phase']}",
                value=f"{t['title'][:100]}...\n**Sponsor:** {t['sponsor']}\n[View Trial]({t['link']})",
                inline=False
            )
    else:
        embed_trials.add_field(name="Status", value="No trials found", inline=False)

    await ctx.send(embed=embed_trials)
    await ctx.send("✅ **Research dump complete.** Use `!dump` for full export.")

@bot.command(name='pdufa')
async def pdufa_scan(ctx):
    """Scans for upcoming PDUFA dates and cross-references with cash runway."""
    await ctx.send("💊 **PDUFA / TRIAL DATA SCANNER** - Scanning FDA catalysts + checking cash runway...")

    pdufa_data = await asyncio.to_thread(
        research_agent.get_pdufa_with_financials, days_ahead=60
    )

    # Cash-runway flags embed
    if pdufa_data.get('alerts'):
        embed_risk = discord.Embed(
            title="⚠️ CASH RUNWAY RISK NOTES",
            description="Companies with upcoming catalysts but low cash runway",
            color=0xe74c3c
        )
        for alert in pdufa_data['alerts']:
            embed_risk.add_field(
                name=f"{alert['risk_emoji']} {alert['ticker']} — {alert['risk_level']} RISK",
                value=f"Cash: {alert['cash']} | Burn: {alert['burn']}/Q\nRunway: {alert['runway']} | Debt: {alert['debt']}",
                inline=False
            )
        await ctx.send(embed=embed_risk)

    # Cash Runway Summary
    if pdufa_data.get('financials'):
        embed_cash = discord.Embed(
            title="💰 CASH RUNWAY SUMMARY",
            description="GREEN (>6Q) | YELLOW (4-6Q) | RED (<4Q)",
            color=0x2ecc71
        )
        for ticker, data in sorted(pdufa_data['financials'].items()):
            risk_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(data['risk_level'], "⚪")
            runway = f"{data['runway_quarters']}Q" if data['runway_quarters'] < 999 else "CF+"
            embed_cash.add_field(
                name=f"{risk_emoji} {ticker}",
                value=f"Cash: {data['cash_formatted']}\nBurn: {data['burn_formatted']}/Q\nRunway: {runway}\nMCap: {data['mcap_formatted']}",
                inline=True
            )
        await ctx.send(embed=embed_cash)

    # Top Catalysts
    catalysts = pdufa_data.get('catalysts', [])
    if catalysts:
        embed_cat = discord.Embed(
            title="📋 UPCOMING FDA CATALYSTS (60-day window)",
            description=f"Found {len(catalysts)} catalysts. Showing top 10.",
            color=0x3498db
        )
        for c in catalysts[:10]:
            priority_tag = " ⭐" if c['is_priority'] else ""
            days_str = f" ({c['days_until']}d)" if c['days_until'] is not None else ""
            title_text = f"{c['title'][:80]}{priority_tag}{days_str}"
            detail = f"Source: {c['source']}"
            if c.get('sponsor'):
                detail += f"\nSponsor: {c['sponsor']}"
            if c.get('status') and c['status'] != 'NEWS':
                detail += f" | {c['status']}"
            if c.get('phase'):
                detail += f" | {c['phase']}"
            embed_cat.add_field(name=title_text, value=detail, inline=False)
        await ctx.send(embed=embed_cat)

    await ctx.send("✅ **PDUFA scan complete.** Use `!dump` for full export with financial data.")
@bot.command(name='dips')
async def dip_scanner(ctx):
    """Market context dashboard — five measurements combined."""
    await ctx.send("📊 **MARKET CONTEXT SCANNER** — Checking five measurement families...")

    # 1. Insider Selling Clusters
    await ctx.send("ℹ️ Scanning insider clusters...")
    clusters = await asyncio.to_thread(
        sec_agent.detect_insider_clusters, days_back=30
    )
    if clusters:
        embed_ins = discord.Embed(
            title="👤 INSIDER CLUSTERS",
            description="Distinct Form 4 filers grouped by transaction-code direction",
            color=0xe74c3c
        )
        for c in clusters[:8]:
            emoji = {"HIGH": "🚨", "MEDIUM": "⚠️"}.get(c['alert_level'], "ℹ️")
            direction = c.get('cluster_direction') or 'non-directional'
            embed_ins.add_field(
                name=f"{emoji} {c['ticker']} — {c['alert_level']}",
                value=f"{c['insider_count']} filers ({c.get('filing_count', '?')} filings) | {direction}",
                inline=True
            )
        await ctx.send(embed=embed_ins)
    else:
        await ctx.send("✅ No insider clusters detected.")

    # 2. Options Flow (Put/Call)
    await ctx.send("ℹ️ Scanning options flow...")
    options_data = await asyncio.to_thread(watcher_agent.get_all_options_flow)
    bearish_options = {t: d for t, d in options_data.items() if d.get('alerts')}
    if bearish_options:
        embed_opt = discord.Embed(
            title="📈 OPTIONS ACTIVITY MEASUREMENTS",
            description="Put/call ratios and completed-session contract activity",
            color=0xff9800
        )
        for ticker, data in bearish_options.items():
            alerts_text = "\n".join(data['alerts'])
            embed_opt.add_field(
                name=f"{ticker} (exp: {data.get('expiration', '?')})",
                value=f"P/C Vol: {data['put_call_vol_ratio']} | P/C OI: {data['put_call_oi_ratio']}\n{alerts_text}",
                inline=False
            )
        await ctx.send(embed=embed_opt)
    else:
        await ctx.send("✅ No unusual options flow detected.")

    # 3. Earnings Calendar
    await ctx.send("ℹ️ Scanning earnings calendar...")
    earnings = await asyncio.to_thread(watcher_agent.get_earnings_calendar)
    if earnings:
        embed_earn = discord.Embed(
            title="📅 EARNINGS CALENDAR",
            description="Upcoming earnings for watchlist tickers",
            color=0x9c27b0
        )
        for e in earnings:
            emoji = "⚠️" if e.get('is_imminent') else "📝" if e.get('is_past_week') else "📅"
            alert_text = f" — {e['alert']}" if e.get('alert') else ""
            embed_earn.add_field(
                name=f"{emoji} {e['ticker']}",
                value=f"{e['earnings_date']} ({e['days_until']:+d} days){alert_text}",
                inline=True
            )
        await ctx.send(embed=embed_earn)
    else:
        await ctx.send("ℹ️ No earnings dates found for watchlist.")

    # 4. RSI Divergence (already in technicals, just surface them)
    technicals = await asyncio.to_thread(watcher_agent.get_all_technicals)
    divergences = {t: d for t, d in technicals.items() if d.get('rsi_divergence')}
    if divergences:
        embed_div = discord.Embed(
            title="📉 RSI DIVERGENCE MEASUREMENTS",
            description="Price vs momentum mismatch — potential reversal",
            color=0xf44336
        )
        for ticker, data in divergences.items():
            div_type = data['rsi_divergence']
            emoji = "🚨" if div_type == "BEARISH" else "🟢"
            embed_div.add_field(
                name=f"{emoji} {ticker} — {div_type} DIVERGENCE",
                value=f"RSI: {data.get('rsi', '?')} | Trend: {data.get('trend', '?')}",
                inline=True
            )
        await ctx.send(embed=embed_div)
    else:
        await ctx.send("✅ No RSI divergences detected.")

    # 5. Sector Rotation
    rotation = await asyncio.to_thread(watcher_agent.get_sector_rotation)
    embed_rot = discord.Embed(
        title="🔄 SECTOR ROTATION",
        description=f"Regime label: **{rotation.get('signal', 'N/A')}**",
        color=0x2196f3
    )
    embed_rot.add_field(
        name="Growth Basket (5d)",
        value=f"{rotation.get('growth_5d', 0):+.1f}%",
        inline=True
    )
    embed_rot.add_field(
        name="Defensive Basket (5d)",
        value=f"{rotation.get('defensive_5d', 0):+.1f}%",
        inline=True
    )
    embed_rot.add_field(
        name="Spread (Def-Growth)",
        value=f"{rotation.get('spread_5d', 0):+.1f}%",
        inline=True
    )
    embed_rot.add_field(
        name="20-day Trend",
        value=f"Growth: {rotation.get('growth_20d', 0):+.1f}% | Def: {rotation.get('defensive_20d', 0):+.1f}% | Spread: {rotation.get('spread_20d', 0):+.1f}%",
        inline=False
    )
    if rotation.get('alerts'):
        for alert in rotation['alerts']:
            embed_rot.add_field(name="Threshold note", value=alert, inline=False)
    await ctx.send(embed=embed_rot)

    await ctx.send("✅ **Context scan complete.** Use `!dump` for the full data export.")

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if TOKEN and GUILD_ID:
        bot.run(TOKEN)
    elif TOKEN:
        print("Error: DISCORD_GUILD_ID is required when DISCORD_TOKEN is set")
    else:
        print("Error: DISCORD_TOKEN not found in .env")
