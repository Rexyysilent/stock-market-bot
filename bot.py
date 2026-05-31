import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
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

# Memory Storage (Simple dict for now)
# Structure: {channel_id: [{'role': 'user', 'content': '...'}]}
chat_history = {}

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord and the Council is ready.')

@bot.event
async def on_message(message):
    # Don't let the bot reply to itself
    if message.author == bot.user:
        return

    # Check if the bot is mentioned
    if bot.user.mentioned_in(message):
        # Remove the mention from the text like "<@12345>"
        user_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        
        if not user_text:
            return # Ignore empty mentions

        # Get History
        cid = message.channel.id
        if cid not in chat_history:
            chat_history[cid] = []
        
        # Keep history short (last 10 turns)
        history = chat_history[cid][-10:]

        async with message.channel.typing():
            # Fetch live context from ALL sub-agents
            await message.channel.send("*Gathering market intelligence from all sources...*")
            
            # Social whispers (top 15)
            whispers = social_agent.get_whisper()
            whisper_context = "\n".join(whispers[:15]) if whispers else ""
            
            # Research data (CEO.ca + ClinicalTrials)
            ceo_signals = research_agent.get_ceo_ca_signals()
            trials = research_agent.get_clinical_trials()
            
            # Build research context
            research_context = "\n--- URANIUM/CEO.CA INTEL ---\n"
            for s in ceo_signals[:8]:
                research_context += f"[{s['ticker']}] {s['title'][:100]}\n"
            
            research_context += "\n--- CLINICAL TRIALS (CRISPR/VERTEX) ---\n"
            for t in trials[:5]:
                research_context += f"[{t['nct_id']}] {t['title'][:80]} - {t['status']}\n"
            
            # X/Twitter intel
            twitter_data = twitter_agent.get_twitter_intel(max_queries=3)
            research_context += "\n--- X/TWITTER SIGNALS ---\n"
            for tw in twitter_data[:5]:
                research_context += f"[X/{tw['query']}] {tw['title'][:80]}\n"
            
            # Combine all context
            live_context = whisper_context + "\n" + research_context if whisper_context else research_context
            
            response = analyst_agent.chat_with_memory(user_text, history, live_context=live_context)
        
        # Update History
        chat_history[cid].append({'role': 'user', 'content': user_text})
        chat_history[cid].append({'role': 'assistant', 'content': response})
        
        await message.channel.send(response)

    # IMPORTANT: We must process commands too!
    await bot.process_commands(message)

@bot.command(name='report')
async def daily_report(ctx):
    """Generates a FULL raw data market report - no summarization, all details."""
    await ctx.send("🔮 **FULL INTELLIGENCE DUMP** - Gathering ALL data from sub-agents...")
    
    # 1. Gather ALL Data
    headlines = news_agent.get_global_headlines()
    whispers = social_agent.get_whisper() or []
    prices = watcher_agent.get_full_report()
    
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
            title=f"👂 SOCIAL SIGNALS (Part {i+1}/{min(len(whisper_chunks), 3)})", 
            color=colors[i % len(colors)]
        )
        embed_whisper.add_field(name="Top Whispers by Engagement", value=chunk, inline=False)
        await ctx.send(embed=embed_whisper)
    
    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY STATS
    # ═══════════════════════════════════════════════════════════════════
    embed_summary = discord.Embed(title="📊 INTELLIGENCE SUMMARY", color=0x95a5a6)
    embed_summary.add_field(name="Data Collected", value=f"• {len(headlines)} headlines\n• {len(whispers)} social signals\n• {len(prices)} price points", inline=True)
    embed_summary.add_field(name="Commands", value="`!whisper` - More signals\n`!dump` - Export for Gemini\n`!analyze <ticker>` - Deep dive", inline=True)
    embed_summary.set_footer(text="Raw data dump complete. Use !dump to export for deeper AI analysis.")
    await ctx.send(embed=embed_summary)

@bot.command(name='analyze')
async def analyze_ticker(ctx, query: str):
    """Quick bull/bear analysis for a topic/ticker."""
    await ctx.send(f"🐂 vs 🐻 Debating **{query}**...")
    
    sentiment = analyst_agent.analyze_sentiment(f"Quick take on {query}")
    
    # Split into chunks if too long for Discord
    if len(sentiment) > 1900:
        parts = [sentiment[i:i+1900] for i in range(0, len(sentiment), 1900)]
        for part in parts:
            await ctx.send(part)
    else:
        await ctx.send(sentiment)

@bot.command(name='debate')
async def full_debate(ctx, ticker: str):
    """Full bull vs bear debate with live market data."""
    ticker = ticker.upper()
    await ctx.send(f"⚖️ **BULL vs BEAR DEBATE: {ticker}** — Gathering live data...")

    # Gather live market context
    market_data_lines = []
    
    # Price
    price_str = watcher_agent.get_stock_price(ticker)
    if price_str:
        market_data_lines.append(f"Current Price: {price_str}")
    
    # Technicals
    tech = watcher_agent.check_technical_indicators(ticker)
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
            market_data_lines.append("Alerts: " + " | ".join(tech['alerts']))
    
    # Options flow
    options = watcher_agent.get_options_flow(ticker)
    if not options.get("error"):
        market_data_lines.append(f"Options P/C Vol Ratio: {options.get('put_call_vol_ratio', 'N/A')}")
        market_data_lines.append(f"Options P/C OI Ratio: {options.get('put_call_oi_ratio', 'N/A')}")
        if options.get('alerts'):
            market_data_lines.append("Options Alerts: " + " | ".join(options['alerts']))
    
    market_data = "\n".join(market_data_lines) if market_data_lines else "No live data available."
    
    await ctx.send(f"📊 Data gathered. Sending to the debaters...")

    # Run the debate
    result = analyst_agent.debate(ticker, market_data)

    # Bull embed
    embed_bull = discord.Embed(
        title=f"🐂 THE BULL on {ticker}",
        description=result['bull_case'][:4000],
        color=0x27ae60  # Green
    )
    await ctx.send(embed=embed_bull)

    # Bear embed
    embed_bear = discord.Embed(
        title=f"🐻 THE BEAR on {ticker}",
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
    whispers = social_agent.get_whisper()
    await ctx.send("**Latest Whispers:**\n" + "\n".join(whispers[:5]))

@bot.command(name='dump')
async def dump_data(ctx):
    """Exports all gathered intelligence to a file for Gemini/NotebookLM."""
    await ctx.send("Dumping brain contents to file... 🧠 -> 📂")
    filename = export_for_notebooklm()
    await ctx.send(file=discord.File(filename))
    await ctx.send(
        f"Here is the raw data. **Upload this to Google NotebookLM** to analyze patterns we might have missed.\n\n"
        f"💡 **REMINDER:** Also ask **Grok** (X app) for real-time Twitter/X intel!\n"
        f"Example: *'What are the top tweets about $TSLA today?'*"
    )

@bot.command(name='research')
async def research_dump(ctx):
    """Shows CEO.ca uranium signals and ClinicalTrials.gov CRISPR data."""
    await ctx.send("🔬 **RESEARCH AGENT** - Fetching specialized intelligence...")
    
    # CEO.ca / Uranium
    await ctx.send("⛏️ Fetching CEO.ca / Uranium signals (UUUU, CCJ, NXE, DNN)...")
    ceo_signals = research_agent.get_ceo_ca_signals()
    
    embed_ceo = discord.Embed(title="⛏️ CEO.CA / URANIUM INTELLIGENCE", color=0x2ecc71)
    embed_ceo.set_footer(text="Focus: Core samples, geology maps, permit delays")
    
    if ceo_signals:
        ceo_text = ""
        for s in ceo_signals[:10]:
            line = f"**[{s['ticker']}]** {s['title'][:80]}...\n"
            if len(ceo_text) + len(line) < 1000:
                ceo_text += line
        embed_ceo.add_field(name="Latest Signals", value=ceo_text or "No signals found", inline=False)
    else:
        embed_ceo.add_field(name="Status", value="No signals found", inline=False)
    
    await ctx.send(embed=embed_ceo)
    
    # Clinical Trials
    await ctx.send("💉 Fetching ClinicalTrials.gov (CRISPR, Vertex, Gene Editing)...")
    trials = research_agent.get_clinical_trials()
    
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
    await ctx.send("💊 **PDUFA CATALYST SCANNER** - Scanning FDA catalysts + checking cash runway...")
    
    pdufa_data = research_agent.get_pdufa_with_financials(days_ahead=60)
    
    # Risk Alerts embed
    if pdufa_data.get('alerts'):
        embed_risk = discord.Embed(
            title="🚨 BANKRUPTCY RISK ALERTS",
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
    """Dip anticipation dashboard — 5 signals combined."""
    await ctx.send("📉 **DIP ANTICIPATION SCANNER** — Checking 5 bearish signals...")

    # 1. Insider Selling Clusters
    await ctx.send("ℹ️ Scanning insider clusters...")
    clusters = sec_agent.detect_insider_clusters(days_back=30)
    if clusters:
        embed_ins = discord.Embed(
            title="👤 INSIDER SELLING CLUSTERS",
            description="Multiple Form 4 filings in 30 days = cluster selling",
            color=0xe74c3c
        )
        for c in clusters[:8]:
            emoji = "🚨" if c['alert_level'] == 'HIGH' else "⚠️"
            embed_ins.add_field(
                name=f"{emoji} {c['ticker']} — {c['alert_level']}",
                value=f"{c['insider_count']} Form 4 filings | {c['unique_dates']} unique dates",
                inline=True
            )
        await ctx.send(embed=embed_ins)
    else:
        await ctx.send("✅ No insider selling clusters detected.")

    # 2. Options Flow (Put/Call)
    await ctx.send("ℹ️ Scanning options flow...")
    options_data = watcher_agent.get_all_options_flow()
    bearish_options = {t: d for t, d in options_data.items() if d.get('alerts')}
    if bearish_options:
        embed_opt = discord.Embed(
            title="📈 OPTIONS FLOW ALERTS",
            description="Put/Call ratio signals — high P/C = bearish hedging",
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
    earnings = watcher_agent.get_earnings_calendar()
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
    technicals = watcher_agent.get_all_technicals()
    divergences = {t: d for t, d in technicals.items() if d.get('rsi_divergence')}
    if divergences:
        embed_div = discord.Embed(
            title="📉 RSI DIVERGENCE SIGNALS",
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
    rotation = watcher_agent.get_sector_rotation()
    embed_rot = discord.Embed(
        title="🔄 SECTOR ROTATION",
        description=f"Signal: **{rotation.get('signal', 'N/A')}**",
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
            embed_rot.add_field(name="🚨 Alert", value=alert, inline=False)
    await ctx.send(embed=embed_rot)

    await ctx.send("✅ **Dip scan complete.** Use `!dump` for full export with all signals.")

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Error: DISCORD_TOKEN not found in .env")
