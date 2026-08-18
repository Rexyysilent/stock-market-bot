/**
 * MARKETBOT DASHBOARD — App Logic
 * Fetches /api/brief (JSON) and /api/txt (raw) from serve_dump.py
 * Populates DOM with cards, alerts, and lists.
 */

let DATA = null;
let RAW_TEXT = '';

function appendText(parent, value) {
    parent.appendChild(document.createTextNode(String(value ?? '')));
}

function appendTag(parent, className, value) {
    const tag = document.createElement('span');
    tag.className = className;
    tag.textContent = String(value ?? '');
    parent.appendChild(tag);
    return tag;
}

function safeExternalUrl(value) {
    if (!value) return null;
    try {
        const parsed = new URL(String(value), window.location.origin);
        return parsed.protocol === 'http:' || parsed.protocol === 'https:'
            ? parsed.href : null;
    } catch (_error) {
        return null;
    }
}

function appendExternalLink(parent, value, label) {
    parent.appendChild(document.createElement('br'));
    const safeUrl = safeExternalUrl(value);
    if (!safeUrl) {
        appendText(parent, label);
        return;
    }
    const link = document.createElement('a');
    link.href = safeUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = String(label);
    parent.appendChild(link);
}

function setupCopyButtons() {
    ['copy-all-btn', 'fab-copy'].forEach(id => {
        const button = document.getElementById(id);
        if (button) button.addEventListener('click', copyRawDump);
    });
}

// ═══ INIT ═══
document.addEventListener('DOMContentLoaded', async () => {
    setupTabs();
    setupCopyButtons();
    await loadData();
});

// ═══ TAB SWITCHING ═══
function setupTabs() {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(`panel-${tab.dataset.tab}`).classList.add('active');
        });
    });
}

// ═══ DATA LOADING ═══
async function loadData() {
    try {
        const [jsonRes, txtRes] = await Promise.all([
            fetch('/api/brief'),
            fetch('/api/txt')
        ]);
        DATA = await jsonRes.json();
        RAW_TEXT = await txtRes.text();

        renderAll();
    } catch (err) {
        console.error('Failed to load data:', err);
        document.getElementById('raw-dump').textContent = 'Error loading data. Is serve_dump.py running?';
    }
}

// ═══ RENDER ALL ═══
function renderAll() {
    if (!DATA) return;

    // Header
    const ts = DATA.generated_at ? new Date(DATA.generated_at).toLocaleString() : '—';
    document.getElementById('timestamp').textContent = ts;
    const pt = DATA.pipeline_time_seconds;
    document.getElementById('pipeline-time').textContent = pt ? `⚡ ${pt}s` : '—';

    // Prices tab
    renderPrices();
    renderTechAlerts();
    renderSectorRotation();

    // Measurements tab
    renderOptionAnomalies();
    renderInstrumentSpreads();
    renderContrarian();
    renderOptionsFlow();

    // Intel tab
    renderHeadlines();
    renderSECFilings();
    renderClinical();
    renderCashRunway();
    renderCashMonitor();
    renderCEO();
    renderWhispers();
    renderTwitter();
    renderInsiders();

    // Raw tab
    document.getElementById('raw-dump').textContent = RAW_TEXT;
}

// ═══ PRICES ═══
function renderPrices() {
    const grid = document.getElementById('price-grid');
    const prices = DATA.sections?.prices || [];
    const techs = {};
    (DATA.sections?.technicals || []).forEach(t => { techs[t.ticker] = t; });

    document.getElementById('ticker-count').textContent = `${prices.length} tickers`;
    grid.innerHTML = '';

    prices.forEach(p => {
        // Schema 2.0: price and change_pct are numeric (no longer a "$X (🟢 +Y%)" string)
        const priceNum = typeof p.price === 'number' ? p.price : parseFloat(p.price);
        const priceVal = Number.isFinite(priceNum) ? priceNum.toFixed(2) : '—';
        const chg = typeof p.change_pct === 'number' ? p.change_pct : parseFloat(p.change_pct);
        const hasChg = Number.isFinite(chg);

        const isUp = hasChg && chg > 0;
        const isDown = hasChg && chg < 0;
        const cls = isUp ? 'positive' : isDown ? 'negative' : 'neutral';
        const changeCls = isUp ? 'up' : isDown ? 'down' : 'flat';

        // Clean change display
        const cleanChange = hasChg ? `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%` : '';

        const tech = techs[p.ticker] || {};
        const rsi = tech.rsi || 50;
        const rsiColor = rsi >= 70 ? 'var(--accent-red)' : rsi <= 30 ? 'var(--accent-green)' : 'var(--accent-blue)';

        const card = document.createElement('div');
        card.className = `price-card ${cls}`;
        card.innerHTML = `
            <div class="ticker">${p.ticker}</div>
            <div class="price">$${priceVal}</div>
            <div class="change ${changeCls}">${cleanChange}</div>
            <div class="rsi-bar"><div class="rsi-fill" style="width:${rsi}%;background:${rsiColor}"></div></div>
            <div class="rsi-label">RSI ${rsi} ${tech.trend ? '· ' + tech.trend.split(' ')[0] : ''}</div>
        `;
        grid.appendChild(card);
    });
}

// ═══ TECHNICAL INDICATORS ═══
function renderTechAlerts() {
    const container = document.getElementById('tech-alerts-list');
    const techs = DATA.sections?.technicals || [];
    const alertTechs = techs.filter(t => t.alerts && t.alerts.length > 0);

    document.getElementById('alert-count').textContent = alertTechs.length;
    container.innerHTML = '';

    if (alertTechs.length === 0) {
        container.innerHTML = '<div class="empty-state">No technical thresholds crossed</div>';
        return;
    }

    alertTechs.forEach(t => {
        t.alerts.forEach(alert => {
            const type = alert.includes('🚨') || alert.includes('DEATH') || alert.includes('DROPPING') ? 'danger'
                : alert.includes('⚠️') || alert.includes('OVERBOUGHT') ? 'warning'
                : alert.includes('🟢') || alert.includes('GOLDEN') || alert.includes('BULLISH') ? 'success' : 'info';
            const item = document.createElement('div');
            item.className = `alert-item ${type}`;
            item.innerHTML = `<div class="alert-title">${t.ticker}</div><div class="alert-meta">${alert}</div>`;
            container.appendChild(item);
        });
    });
}

// ═══ SECTOR ROTATION ═══
function renderSectorRotation() {
    const container = document.getElementById('sector-rotation');
    const sr = DATA.sections?.sector_rotation || {};
    const signal = sr.signal || 'N/A';
    const signalColor = signal === 'RISK_OFF' ? 'var(--accent-red)'
        : signal === 'RISK_ON' ? 'var(--accent-green)'
        : signal === 'CAUTIOUS' ? 'var(--accent-yellow)' : 'var(--text-muted)';

    container.innerHTML = `
        <div class="row"><span class="label">Regime label</span><span class="value" style="color:${signalColor}">${signal}</span></div>
        <div class="row"><span class="label">Growth 5d</span><span class="value">${sr.growth_5d ?? '—'}%</span></div>
        <div class="row"><span class="label">Defensive 5d</span><span class="value">${sr.defensive_5d ?? '—'}%</span></div>
        <div class="row"><span class="label">Spread</span><span class="value">${sr.spread_5d ?? '—'}%</span></div>
    `;
}

// ═══ OPTION CONTRACT VOLUME/OI ANOMALIES ═══
function renderOptionAnomalies() {
    const container = document.getElementById('option-anomaly-list');
    const of = DATA.sections?.options_flow || {};
    const anomalies = [];
    Object.values(of).forEach(d => (d?.option_contract_volume_oi_anomaly || []).forEach(a => anomalies.push(a)));
    document.getElementById('option-anomaly-count').textContent = anomalies.length;
    container.innerHTML = '';

    if (anomalies.length === 0) {
        container.innerHTML = '<div class="empty-state">No option contract volume/OI anomalies</div>';
        return;
    }

    anomalies.forEach(s => {
        // 2.6 names this honestly: last price × completed-session volume × 100.
        const rawNotional = s.notional_estimate ?? s.premium;
        const notional = s.premium_fmt || (typeof rawNotional === 'number'
            ? '$' + (rawNotional / 1e6).toFixed(1) + 'M' : (rawNotional ?? '—'));
        const item = document.createElement('div');
        item.className = 'alert-item danger';
        item.innerHTML = `
            <div class="alert-title">🎯 ${s.ticker} $${s.strike}${s.type[0]}</div>
            <div class="alert-meta">DTE=${s.dte} · Vol/OI=${s.vol_oi_ratio}x · Est. notional=${notional} · Exp: ${s.expiration}</div>
        `;
        container.appendChild(item);
    });
}

// ═══ INSTRUMENT RELATIVE-RETURN SPREAD ═══
function renderInstrumentSpreads() {
    const container = document.getElementById('instrument-spread-list');
    const spread = DATA.sections?.instrument_relative_return_spread || {};
    const pairs = spread.pairs || [];
    container.innerHTML = '';

    if (pairs.length === 0) {
        container.innerHTML = '<div class="empty-state">No instrument spread data</div>';
        return;
    }

    pairs.forEach(p => {
        const type = p.threshold_crossed ? 'danger' : 'success';
        const status = p.threshold_crossed ? '🚨 THRESHOLD CROSSED' : '✅ NORMAL';
        const item = document.createElement('div');
        item.className = `alert-item ${type}`;
        item.innerHTML = `
            <div class="alert-title">${status} — ${p.commodity}</div>
            <div class="alert-meta">
                Physical (${p.physical_ticker}): ${p.physical_5d_chg > 0 ? '+' : ''}${p.physical_5d_chg}% · 
                Paper (${p.paper_ticker}): ${p.paper_5d_chg > 0 ? '+' : ''}${p.paper_5d_chg}% · 
                Spread: ${p.spread > 0 ? '+' : ''}${p.spread}%
            </div>
        `;
        container.appendChild(item);
    });
}

// ═══ RETAIL LANGUAGE INTENSITY ═══
function renderContrarian() {
    const container = document.getElementById('contrarian-list');
    const data = DATA.sections?.retail_contrarian || {};
    const subs = data.subreddits || [];
    const biz = data.biz || [];
    container.innerHTML = '';

    subs.forEach(s => {
        const type = s.is_topped ? 'danger' : 'success';
        const status = s.is_topped ? 'ELEVATED' : 'BASELINE';
        const item = document.createElement('div');
        item.className = `alert-item ${type}`;
        item.innerHTML = `
            <div class="alert-title">${status} — r/${s.subreddit}</div>
            <div class="alert-meta">Euphoria: ${(s.euphoria_ratio * 100).toFixed(0)}% (${s.euphoric_posts}/${s.total_posts} posts)</div>
        `;
        container.appendChild(item);
    });

    if (biz.length > 0) {
        const bizItem = document.createElement('div');
        bizItem.className = 'alert-item info';
        const title = document.createElement('div');
        title.className = 'alert-title';
        title.textContent = `/biz/ — ${biz.length} threads scanned`;
        const meta = document.createElement('div');
        meta.className = 'alert-meta';
        meta.textContent = biz.slice(0, 3)
            .map(b => b.subject || String(b.comment || '').slice(0, 60))
            .join(' · ');
        bizItem.append(title, meta);
        container.appendChild(bizItem);
    }

    if (subs.length === 0 && biz.length === 0) {
        container.innerHTML = '<div class="empty-state">No retail-language data</div>';
    }
}

// ═══ OPTIONS FLOW ═══
function renderOptionsFlow() {
    const container = document.getElementById('options-list');
    const flow = DATA.sections?.options_flow || {};
    container.innerHTML = '';

    const entries = Object.entries(flow).filter(([_, d]) => d.alerts && d.alerts.length > 0);
    if (entries.length === 0) {
        container.innerHTML = '<div class="empty-state">No options thresholds crossed</div>';
        return;
    }

    entries.forEach(([ticker, d]) => {
        d.alerts.forEach(alert => {
            const type = alert.includes('🚨') || alert.includes('VOLUME/OI ANOMALY') ? 'danger'
                : alert.includes('⚠️') ? 'warning'
                : alert.includes('🟢') ? 'success' : 'info';
            const item = document.createElement('div');
            item.className = `alert-item ${type}`;
            item.innerHTML = `<div class="alert-title">${ticker}</div><div class="alert-meta">${alert}</div>`;
            container.appendChild(item);
        });
    });
}

// ═══ HEADLINES ═══
function renderHeadlines() {
    renderTextList('headlines-list', DATA.sections?.headlines || [], d => d.text);
}

// ═══ SEC FILINGS ═══
function renderSECFilings() {
    const container = document.getElementById('sec-list');
    const filings = DATA.sections?.sec_filings || [];
    container.innerHTML = '';

    if (filings.length === 0) {
        container.innerHTML = '<div class="empty-state">No recent SEC filings</div>';
        return;
    }

    filings.forEach(f => {
        const item = document.createElement('div');
        item.className = 'text-item';
        appendTag(item, 'source-tag', `${f.ticker ?? ''}/${f.form_type ?? ''}`);
        appendText(item, ` ${f.description ?? ''}`);
        appendExternalLink(item, f.link, `${f.date ?? ''} → View Filing`);
        container.appendChild(item);
    });
}

// ═══ PDUFA ═══
function renderClinical() {
    // Schema 2.0: section is `clinical_catalysts` (was the non-existent `pdufa_catalysts`)
    const container = document.getElementById('clinical-list');
    const cats = DATA.sections?.clinical_catalysts || [];
    const countEl = document.getElementById('clinical-count');
    if (countEl) countEl.textContent = cats.length;
    container.innerHTML = '';

    if (cats.length === 0) {
        container.innerHTML = '<div class="empty-state">No upcoming clinical catalysts</div>';
        return;
    }

    // Priority pinned to top, then soonest target date first
    const sorted = [...cats].sort((a, b) => {
        if (!!b.is_priority !== !!a.is_priority) return (b.is_priority ? 1 : 0) - (a.is_priority ? 1 : 0);
        return (a.days_until ?? 1e9) - (b.days_until ?? 1e9);
    });

    sorted.slice(0, 20).forEach(c => {
        const eta = Number.isFinite(c.days_until)
            ? `${c.days_until}d (${c.target_date})` : (c.target_date || '');
        const item = document.createElement('div');
        item.className = 'text-item';
        appendTag(item, 'source-tag', c.source || 'ClinicalTrials.gov');
        appendText(item, ' ');
        const safeUrl = safeExternalUrl(c.link);
        if (safeUrl) {
            const link = document.createElement('a');
            link.href = safeUrl;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = String(c.title ?? '');
            item.appendChild(link);
        } else {
            appendText(item, c.title);
        }
        if (c.is_priority) {
            appendText(item, ' ');
            appendTag(item, 'verified-tag', 'WATCHLIST');
        }
        if (c.sponsor) {
            item.appendChild(document.createElement('br'));
            const meta = appendTag(
                item, '',
                `${c.sponsor} · ${c.status || ''} · ${c.phase || ''}${eta ? ' · ETA ' + eta : ''}`
            );
            meta.style.color = 'var(--text-muted)';
            meta.style.fontSize = '11px';
        }
        container.appendChild(item);
    });
}

// ═══ CEO.CA ═══
function renderCashRunway() {
    // Schema 2.0: `cash_runway_alerts` — biotech burn/runway risk (RED/YELLOW)
    const container = document.getElementById('cash-runway-list');
    const alerts = DATA.sections?.cash_runway_alerts || [];
    const countEl = document.getElementById('cash-runway-count');
    if (countEl) countEl.textContent = alerts.length;
    container.innerHTML = '';

    if (alerts.length === 0) {
        container.innerHTML = '<div class="empty-state">No cash runway risks flagged</div>';
        return;
    }

    // RED first, then shortest runway
    const rank = r => (r === 'RED' ? 0 : r === 'YELLOW' ? 1 : 2);
    const sorted = [...alerts].sort((a, b) => {
        const d = rank(a.risk_level) - rank(b.risk_level);
        return d !== 0 ? d : (a.runway_quarters ?? 1e9) - (b.runway_quarters ?? 1e9);
    });

    const fmt = v => (typeof v === 'number' ? '$' + v.toFixed(1) + 'M' : '—');
    sorted.forEach(a => {
        const cls = a.risk_level === 'RED' ? 'danger' : a.risk_level === 'YELLOW' ? 'warning' : 'info';
        const runway = Number.isFinite(a.runway_quarters) ? `${a.runway_quarters}Q runway` : 'runway n/a';
        const cfp = a.cash_flow_positive ? 'CF+' : 'CF−';
        const item = document.createElement('div');
        item.className = `alert-item ${cls}`;
        item.innerHTML = `
            <div class="alert-title">💸 ${a.ticker} · ${a.risk_level} · ${runway}</div>
            <div class="alert-meta">Cash ${fmt(a.cash_musd)} · Burn ${fmt(a.burn_musd)}/q · Debt ${fmt(a.debt_musd)} · MCap ${fmt(a.market_cap_musd)} · ${cfp}</div>
        `;
        container.appendChild(item);
    });
}

function renderCashMonitor() {
    // Schema 2.0: `cash_runway` — full per-ticker burn/runway detail (incl. healthy GREEN)
    const container = document.getElementById('cash-monitor-list');
    const rows = DATA.sections?.cash_runway || [];
    const countEl = document.getElementById('cash-monitor-count');
    if (countEl) countEl.textContent = rows.length;
    container.innerHTML = '';

    if (rows.length === 0) {
        container.innerHTML = '<div class="empty-state">No cash runway data</div>';
        return;
    }

    // RED → YELLOW → GREEN, then shortest runway (nulls last)
    const rank = r => (r === 'RED' ? 0 : r === 'YELLOW' ? 1 : r === 'GREEN' ? 2 : 3);
    const sorted = [...rows].sort((a, b) => {
        const d = rank(a.risk_level) - rank(b.risk_level);
        return d !== 0 ? d : (a.runway_quarters ?? 1e9) - (b.runway_quarters ?? 1e9);
    });

    const fmt = v => (typeof v === 'number' ? '$' + v.toFixed(1) + 'M' : '—');
    sorted.forEach(r => {
        const cls = r.risk_level === 'RED' ? 'danger'
                  : r.risk_level === 'YELLOW' ? 'warning'
                  : r.risk_level === 'GREEN' ? 'success' : 'info';
        // null runway + positive cash flow = self-funding; negative burn = generating cash
        const runway = Number.isFinite(r.runway_quarters) ? `${r.runway_quarters}Q runway`
                     : (r.cash_flow_positive ? '∞ (cash-generative)' : 'runway n/a');
        const burnStr = (typeof r.burn_musd === 'number')
            ? (r.burn_musd < 0 ? `+${fmt(-r.burn_musd)}/q (cash-gen)` : `${fmt(r.burn_musd)}/q burn`)
            : '—';
        const item = document.createElement('div');
        item.className = `alert-item ${cls}`;
        item.innerHTML = `
            <div class="alert-title">🧬 ${r.ticker} · ${r.risk_level} · ${runway}</div>
            <div class="alert-meta">Cash ${fmt(r.cash_musd)} · ${burnStr} · Debt ${fmt(r.debt_musd)} · MCap ${fmt(r.market_cap_musd)}</div>
        `;
        container.appendChild(item);
    });
}

function renderCEO() {
    const container = document.getElementById('ceo-list');
    const signals = DATA.sections?.ceo_ca_signals || [];
    container.innerHTML = '';

    if (signals.length === 0) {
        container.innerHTML = '<div class="empty-state">No CEO.ca signals</div>';
        return;
    }

    signals.forEach(s => {
        const item = document.createElement('div');
        item.className = 'text-item';
        const sourceRecordVerified = s.source_record_verified === true;
        const thresholdQualified = s.threshold_qualified ?? s.verified ?? false;
        appendTag(item, 'source-tag', s.source);
        appendText(item, ` ${s.title ?? ''} `);
        appendTag(
            item,
            sourceRecordVerified ? 'verified-tag' : 'source-tag',
            sourceRecordVerified ? 'DIRECT API' : (s.provenance_status || 'UNKNOWN PROVENANCE')
        );
        appendText(item, ' ');
        appendTag(
            item,
            thresholdQualified ? 'verified-tag' : 'source-tag',
            thresholdQualified ? 'GEOLOGY THRESHOLD' : 'CONTEXT ONLY'
        );
        if (s.grade_tag) {
            item.appendChild(document.createElement('br'));
            const grade = appendTag(item, '', s.grade_tag);
            grade.style.color = 'var(--accent-green)';
            grade.style.fontSize = '11px';
            grade.style.fontFamily = 'var(--font-mono)';
        }
        appendExternalLink(item, s.link, 'View →');
        container.appendChild(item);
    });
}

// ═══ WHISPERS ═══
function renderWhispers() {
    renderTextList('whispers-list', DATA.sections?.social_whispers || [], d => d.text, d => d.source);
}

// ═══ TWITTER ═══
function renderTwitter() {
    const container = document.getElementById('twitter-list');
    const tweets = DATA.sections?.twitter_signals || [];
    container.innerHTML = '';

    if (tweets.length === 0) {
        container.innerHTML = '<div class="empty-state">No Twitter signals</div>';
        return;
    }

    tweets.forEach(tw => {
        const item = document.createElement('div');
        item.className = 'text-item';
        appendTag(item, 'source-tag', `@${tw.account ?? ''}`);
        appendText(item, ` ${tw.title ?? ''}`);
        appendExternalLink(item, tw.link, 'View →');
        container.appendChild(item);
    });
}

// ═══ INSIDERS ═══
function renderInsiders() {
    const container = document.getElementById('insider-list');
    const clusters = DATA.sections?.insider_clusters || [];
    container.innerHTML = '';

    if (clusters.length === 0) {
        container.innerHTML = '<div class="empty-state">No insider selling clusters</div>';
        return;
    }

    clusters.forEach(c => {
        const type = c.alert_level === 'HIGH' ? 'danger' : 'warning';
        const item = document.createElement('div');
        item.className = `alert-item ${type}`;
        const title = document.createElement('div');
        title.className = 'alert-title';
        title.textContent = String(c.ticker ?? '');
        const meta = document.createElement('div');
        meta.className = 'alert-meta';
        meta.textContent = `[${c.alert_level ?? ''}] ${c.insider_count ?? 0} Form 4 filings in ${c.period_days ?? 0}d`;
        item.append(title, meta);
        container.appendChild(item);
    });
}

// ═══ GENERIC TEXT LIST ═══
function renderTextList(containerId, items, textFn, sourceFn) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';

    if (items.length === 0) {
        container.innerHTML = '<div class="empty-state">No data</div>';
        return;
    }

    items.slice(0, 30).forEach(d => {
        const item = document.createElement('div');
        item.className = 'text-item';
        const source = sourceFn ? sourceFn(d) : '';
        if (source) {
            appendTag(item, 'source-tag', source);
        }
        appendText(item, textFn(d));
        container.appendChild(item);
    });
}

// ═══ COPY RAW DUMP ═══
async function copyRawDump() {
    try {
        await navigator.clipboard.writeText(RAW_TEXT);
        const btn = document.getElementById('copy-all-btn');
        if (btn) {
            btn.textContent = '✅ Copied!';
            btn.classList.add('copied');
            setTimeout(() => { btn.textContent = '📋 Copy All'; btn.classList.remove('copied'); }, 3000);
        }
        const fab = document.getElementById('fab-copy');
        fab.textContent = '✅';
        setTimeout(() => { fab.textContent = '📋'; }, 2000);
    } catch (e) {
        // Fallback: select all text
        const el = document.getElementById('raw-dump');
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }
}
