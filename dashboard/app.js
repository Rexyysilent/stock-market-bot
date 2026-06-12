/**
 * MARKET BRIEF DASHBOARD — App Logic
 * Fetches /api/brief (JSON) and /api/txt (raw) from serve_dump.py
 * Populates DOM with cards, alerts, and lists.
 */

let DATA = null;
let RAW_TEXT = '';

// ═══ INIT ═══
document.addEventListener('DOMContentLoaded', async () => {
    setupTabs();
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

    // Arsenal tab
    renderGammaSweeps();
    renderBackwardation();
    renderContrarian();
    renderOptionsFlow();

    // Intel tab
    renderHeadlines();
    renderSECFilings();
    renderPDUFA();
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
        // Schema 2.0: price and change_pct are numeric (or null); older dumps used a formatted string
        let priceVal, changePct = null;
        if (typeof p.price === 'number') {
            priceVal = p.price.toFixed(2);
            changePct = typeof p.change_pct === 'number' ? p.change_pct : null;
        } else {
            const priceMatch = String(p.price || '').match(/\$([0-9.]+)\s*\(([^)]+)\)/);
            priceVal = priceMatch ? priceMatch[1] : (p.price || 'N/A');
            const changeStr = priceMatch ? priceMatch[2].replace(/[🟢🔴⚪]/g, '').trim() : '';
            changePct = changeStr ? parseFloat(changeStr) : null;
        }

        const isUp = changePct !== null && changePct > 0;
        const isDown = changePct !== null && changePct < 0;
        const cls = isUp ? 'positive' : isDown ? 'negative' : 'neutral';
        const changeCls = isUp ? 'up' : isDown ? 'down' : 'flat';
        const cleanChange = changePct !== null ? `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%` : '';

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

// ═══ TECH ALERTS ═══
function renderTechAlerts() {
    const container = document.getElementById('tech-alerts-list');
    const techs = DATA.sections?.technicals || [];
    const alertTechs = techs.filter(t => t.alerts && t.alerts.length > 0);

    document.getElementById('alert-count').textContent = alertTechs.length;
    container.innerHTML = '';

    if (alertTechs.length === 0) {
        container.innerHTML = '<div class="empty-state">No technical alerts triggered</div>';
        return;
    }

    alertTechs.forEach(t => {
        t.alerts.forEach(alert => {
            const upper = alert.toUpperCase();
            const type = alert.includes('🚨') || upper.includes('DEATH CROSS') || upper.includes('FALLING') || upper.includes('DOWNSIDE') ? 'danger'
                : alert.includes('⚠️') || upper.includes('OVERBOUGHT') || upper.includes('PULLBACK') ? 'warning'
                : alert.includes('🟢') || upper.includes('GOLDEN CROSS') || upper.includes('UPSIDE') || upper.includes('OVERSOLD') ? 'success' : 'info';
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
        <div class="row"><span class="label">Signal</span><span class="value" style="color:${signalColor}">${signal}</span></div>
        <div class="row"><span class="label">Growth 5d</span><span class="value">${sr.growth_5d ?? '—'}%</span></div>
        <div class="row"><span class="label">Defensive 5d</span><span class="value">${sr.defensive_5d ?? '—'}%</span></div>
        <div class="row"><span class="label">Spread</span><span class="value">${sr.spread_5d ?? '—'}%</span></div>
    `;
}

// ═══ SHORT-DATED OPTIONS ACTIVITY ═══
function renderGammaSweeps() {
    const container = document.getElementById('gamma-list');
    // Schema 2.0: sweeps live per-ticker under options_flow; older dumps had a top-level section
    let sweeps = DATA.sections?.gamma_sweeps || [];
    if (sweeps.length === 0) {
        const flow = DATA.sections?.options_flow || {};
        sweeps = Object.entries(flow).flatMap(([ticker, d]) =>
            (d.gamma_sweeps || []).map(s => ({ ticker, ...s }))
        );
    }
    document.getElementById('gamma-count').textContent = sweeps.length;
    container.innerHTML = '';

    if (sweeps.length === 0) {
        container.innerHTML = '<div class="empty-state">No short-dated high Vol/OI options activity detected</div>';
        return;
    }

    sweeps.forEach(s => {
        const premium = s.premium_fmt || (typeof s.premium === 'number' ? `$${(s.premium / 1e6).toFixed(2)}M` : '?');
        const item = document.createElement('div');
        item.className = 'alert-item danger';
        item.innerHTML = `
            <div class="alert-title">🎯 ${s.ticker} $${s.strike}${(s.type || '?')[0]}</div>
            <div class="alert-meta">DTE=${s.dte} · Vol/OI=${s.vol_oi_ratio}x · Premium=${premium} · Exp: ${s.expiration}</div>
        `;
        container.appendChild(item);
    });
}

// ═══ BACKWARDATION ═══
function renderBackwardation() {
    const container = document.getElementById('backwardation-list');
    const bwd = DATA.sections?.backwardation || {};
    const pairs = bwd.pairs || [];
    container.innerHTML = '';

    if (pairs.length === 0) {
        container.innerHTML = '<div class="empty-state">No backwardation data</div>';
        return;
    }

    pairs.forEach(p => {
        const type = p.backwardation ? 'danger' : 'success';
        const status = p.backwardation ? '🚨 BACKWARDATION' : '✅ NORMAL';
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

// ═══ RETAIL CONTRARIAN ═══
function renderContrarian() {
    const container = document.getElementById('contrarian-list');
    const data = DATA.sections?.retail_contrarian || {};
    const subs = data.subreddits || [];
    const biz = data.biz || [];
    container.innerHTML = '';

    subs.forEach(s => {
        const type = s.is_topped ? 'danger' : 'success';
        const status = s.is_topped ? '🚨 ELEVATED' : '🟢 BASELINE';
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
        bizItem.innerHTML = `
            <div class="alert-title">/biz/ — ${biz.length} threads scanned</div>
            <div class="alert-meta">${biz.slice(0, 3).map(b => b.subject || b.comment.slice(0, 60)).join(' · ')}</div>
        `;
        container.appendChild(bizItem);
    }

    if (subs.length === 0 && biz.length === 0) {
        container.innerHTML = '<div class="empty-state">No contrarian data</div>';
    }
}

// ═══ OPTIONS FLOW ═══
function renderOptionsFlow() {
    const container = document.getElementById('options-list');
    const flow = DATA.sections?.options_flow || {};
    container.innerHTML = '';

    const entries = Object.entries(flow).filter(([_, d]) => d.alerts && d.alerts.length > 0);
    if (entries.length === 0) {
        container.innerHTML = '<div class="empty-state">No significant options flow alerts</div>';
        return;
    }

    entries.forEach(([ticker, d]) => {
        d.alerts.forEach(alert => {
            const upper = alert.toUpperCase();
            const type = alert.includes('🚨') || upper.includes('STRONGLY PUT-SKEWED') || upper.includes('HEAVY PUT') ? 'danger'
                : alert.includes('⚠️') || upper.includes('PUT-SKEWED') || upper.includes('SHORT-DATED') ? 'warning'
                : alert.includes('🟢') || upper.includes('CALL-SKEWED') ? 'success' : 'info';
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
        item.innerHTML = `
            <span class="source-tag">${f.ticker}/${f.form_type}</span>
            ${f.description}
            <br><a href="${f.link}" target="_blank">${f.date} → View Filing</a>
        `;
        container.appendChild(item);
    });
}

// ═══ CLINICAL CATALYSTS ═══
function renderPDUFA() {
    const container = document.getElementById('pdufa-list');
    // Schema 2.0 renamed pdufa_catalysts to clinical_catalysts
    const cats = DATA.sections?.clinical_catalysts || DATA.sections?.pdufa_catalysts || [];
    container.innerHTML = '';

    if (cats.length === 0) {
        container.innerHTML = '<div class="empty-state">No upcoming clinical catalysts</div>';
        return;
    }

    cats.slice(0, 15).forEach(c => {
        const item = document.createElement('div');
        item.className = 'text-item';
        const priorityTag = c.is_priority ? '<span class="verified-tag">WATCHLIST</span>' : '';
        item.innerHTML = `
            <span class="source-tag">${c.nct_id || c.source || 'PDUFA'}</span>
            ${c.title} ${priorityTag}
            ${c.sponsor ? `<br><span style="color:var(--text-muted);font-size:11px">Sponsor: ${c.sponsor} · ${c.status} · ${c.phase || '?'}</span>` : ''}
        `;
        container.appendChild(item);
    });
}

// ═══ CEO.CA ═══
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
        const verifiedTag = s.verified ? '<span class="verified-tag">VERIFIED</span>' : '';
        const gradeTag = s.grade_tag ? `<br><span style="color:var(--accent-green);font-size:11px;font-family:var(--font-mono)">${s.grade_tag}</span>` : '';
        item.innerHTML = `
            <span class="source-tag">${s.source}</span>
            ${s.title} ${verifiedTag} ${gradeTag}
            <br><a href="${s.link}" target="_blank">View →</a>
        `;
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
        item.innerHTML = `
            <span class="source-tag">@${tw.account}</span>
            ${tw.title}
            <br><a href="${tw.link}" target="_blank">View →</a>
        `;
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
        item.innerHTML = `
            <div class="alert-title">${c.ticker}</div>
            <div class="alert-meta">[${c.alert_level}] ${c.insider_count} Form 4 filings in ${c.period_days}d</div>
        `;
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
        const sourceTag = source ? `<span class="source-tag">${source}</span>` : '';
        item.innerHTML = `${sourceTag}${textFn(d)}`;
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
