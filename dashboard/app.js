/* Local, read-only market evidence viewer. External values are text, never HTML. */
'use strict';
let DATA = null;
let RAW_TEXT = '';
let LOAD_TOKEN = 0;
let SNAPSHOT_LABEL = 'Server snapshot';
const el = id => document.getElementById(id);
const rows = value => Array.isArray(value) ? value.filter(v => v && typeof v === 'object') : [];
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const finite = value => typeof value === 'number' && Number.isFinite(value);
const number = (value, digits = 2) => finite(value) ? value.toFixed(digits) : 'Unavailable';
const percent = value => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : 'Unavailable';
const section = key => DATA?.sections?.[key];
const text = value => value == null ? 'Unknown' : typeof value === 'object' ? JSON.stringify(value) : String(value);

function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value != null) item.textContent = text(value);
    return item;
}
function appendText(parent, value) { parent.append(document.createTextNode(text(value))); }
function appendTag(parent, className, value) { const tag = node('span', className, value); parent.append(tag); return tag; }
function safeExternalUrl(value) {
    if (typeof value !== 'string') return null;
    try {
        const parsed = new URL(value);
        return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : null;
    } catch (_error) { return null; }
}
function appendExternalLink(parent, value, label) {
    const url = safeExternalUrl(value);
    if (!url) { appendText(parent, label); return; }
    const link = node('a', '', label);
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    parent.append(link);
}
function empty(container, message = 'No qualifying records in this brief. Check source coverage before interpreting absence.') {
    container.append(node('div', 'empty-state', message));
}
function card(parent, title, description, kind = 'info') {
    const item = node('div', `alert-item ${kind}`);
    item.append(node('div', 'alert-title', title), node('div', 'alert-meta', description));
    parent.append(item); return item;
}
function reset(id) { const container = el(id); container.replaceChildren(); return container; }
function kv(parent, label, value) {
    const row = node('div', 'row');
    row.append(node('span', 'label', label), node('span', 'value', value)); parent.append(row);
}
function boundedList(id, items, render) {
    const container = reset(id);
    if (!items.length) { empty(container); return; }
    items.slice(0, 200).forEach(item => render(container, item));
    if (items.length > 200) empty(container, `Showing 200 of ${items.length} records. The raw export retains the full set.`);
}
function meta(record) {
    return `Source: ${text(record.source || record.provider)} · As of: ${text(record.as_of)} · Observed: ${text(record.observed_at)}`;
}

async function fetchWithTimeout(path) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch(path, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
        // Consume inside the timeout so a stalled body is also bounded.
        return await response.text();
    } finally { clearTimeout(timer); }
}
async function loadData() {
    const token = ++LOAD_TOKEN;
    el('refresh-btn').disabled = true;
    try {
        const parsed = JSON.parse(await fetchWithTimeout('/api/brief'));
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || !parsed.sections || Array.isArray(parsed.sections) || typeof parsed.sections !== 'object') throw new Error('Unsupported brief: sections object missing');
        if (token !== LOAD_TOKEN) return;
        SNAPSHOT_LABEL = 'Server snapshot';
        DATA = parsed;
        RAW_TEXT = JSON.stringify(DATA, null, 2);
        renderAll();
        let raw;
        try { raw = await fetchWithTimeout('/api/txt'); }
        catch (_error) { raw = 'Human-readable text unavailable. Structured JSON follows.\n\n' + JSON.stringify(parsed, null, 2); }
        if (token === LOAD_TOKEN) { RAW_TEXT = raw; el('raw-dump').textContent = RAW_TEXT; }
    } catch (error) {
        if (token !== LOAD_TOKEN) return;
        el('quality-status').textContent = DATA ? 'REFRESH FAILED: showing the previously loaded snapshot' : 'BRIEF UNAVAILABLE';
        el('quality-status').className = 'quality-banner error';
        el('load-status').textContent = `${error.message}. Select the correct --data-dir or run python marketbot.py demo.`;
        if (!DATA) el('raw-dump').textContent = 'No brief loaded. No empty panel should be interpreted as a quiet market.';
    } finally { if (token === LOAD_TOKEN) el('refresh-btn').disabled = false; }
}
async function loadLocalFile(file) {
    if (!file) return;
    const token = ++LOAD_TOKEN;
    try {
        if (file.size > 32 * 1024 * 1024) throw new Error('Local brief exceeds 32 MiB');
        const content = await file.text();
        const parsed = JSON.parse(content);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || !parsed.sections || Array.isArray(parsed.sections) || typeof parsed.sections !== 'object') throw new Error('Unsupported brief: sections object missing');
        if (token !== LOAD_TOKEN) return;
        DATA = parsed; RAW_TEXT = content; SNAPSHOT_LABEL = `Local file: ${file.name}`;
        renderAll();
        el('load-status').textContent = 'Read locally in this browser. No file upload or provider request was made.';
    } catch (error) {
        if (token === LOAD_TOKEN) el('load-status').textContent = `Local file not loaded: ${error.message}. Previously loaded data, if any, is unchanged.`;
    } finally { if (token === LOAD_TOKEN) el('refresh-btn').disabled = false; }
}
function renderAll() {
    el('timestamp').textContent = `Generated: ${text(DATA.generated_at)} (not source freshness)`;
    el('pipeline-time').textContent = `v${text(DATA.pipeline_version)} · ${number(DATA.pipeline_time_seconds, 1)}s`;
    el('load-status').textContent = '';
    const renderers = [renderQuality, renderPrices, renderTechAlerts, renderSectorRotation,
        renderOptionAnomalies, renderInstrumentSpreads, renderContrarian, renderOptionsFlow,
        renderHeadlines, renderSECFilings, renderClinical, renderCashRunway, renderCashMonitor,
        renderCEO, renderWhispers, renderTwitter, renderInsiders];
    for (const render of renderers) {
        try { render(); }
        catch (error) { el('load-status').textContent += ` ${render.name} could not display this record shape. Inspect raw JSON.`; console.error(error); }
    }
    el('raw-dump').textContent = RAW_TEXT;
}

function renderQuality() {
    const health = object(DATA.health);
    const status = ['OK', 'WARN', 'ERROR'].includes(health.status) ? health.status : 'UNKNOWN';
    const warnings = Array.isArray(health.warnings) ? health.warnings : [];
    const errors = Array.isArray(health.errors) ? health.errors : [];
    const context = object(DATA.run_context);
    const generated = Date.parse(DATA.generated_at);
    const historical = Number.isFinite(generated) && Date.now() - generated > 36 * 3600000;
    el('quality-status').className = `quality-banner ${status.toLowerCase()}`;
    el('quality-status').textContent = `${DATA.demo === true ? 'SYNTHETIC DEMO · ' : ''}${status} · ${warnings.length} warnings · ${errors.length} errors${historical ? ' · Older saved snapshot' : ''}`;
    el('quality-explanation').textContent = 'Observations and threshold crossings, not recommendations or predictive probabilities. Empty output is not evidence of healthy coverage.';
    const summary = reset('quality-summary');
    const prices = rows(section('prices'));
    const techs = rows(section('technicals'));
    const expected = Array.isArray(DATA.universe?.tickers) ? DATA.universe.tickers.length : 'Unknown';
    kv(summary, 'Viewing', SNAPSHOT_LABEL);
    kv(summary, 'Numeric price coverage', `${prices.filter(p => finite(p.price)).length} / ${expected} universe members`);
    kv(summary, 'RSI available', `${techs.filter(t => finite(t.rsi) && t.rsi >= 0 && t.rsi <= 100).length} / ${techs.length} technical records`);
    kv(summary, 'Completed comparison session', context.latest_completed_session || 'Unknown');
    kv(summary, 'Market state at generation', context.market_state || 'Unknown');
    const baselines = techs.filter(t => finite(t.volume_ratio_baseline_n));
    kv(summary, 'Volume baseline maturity', `${baselines.filter(t => t.volume_ratio_baseline_n >= 20).length} mature / ${baselines.length} reported`);
    const issues = reset('quality-issues');
    [...errors, ...warnings].slice(0, 50).forEach(message => issues.append(node('div', 'quality-message', message)));
    if (warnings.length + errors.length > 50) empty(issues, 'Further warnings remain in raw JSON.');
    const sources = reset('source-health');
    for (const [name, state] of Object.entries(object(health.sources))) {
        const details = node('details', 'source-details');
        details.append(node('summary', '', `${name}: ${text(state?.status || state?.provider_status || 'details')}`),
                       node('pre', 'source-json', JSON.stringify(state, null, 2)));
        sources.append(details);
    }
    if (!sources.children.length) empty(sources, 'Source-health detail was not supplied; coverage is unknown.');
    boundedList('confluence-list', rows(section('confluence')), (container, record) => {
        const families = record.families || record.alert_families || [];
        card(container, `${text(record.ticker)} · ${text(record.confluence_score ?? record.score)} distinct families`,
             `${text(families)} · ${meta(record)}. Family count is not a probability.`);
    });
}
function renderPrices() {
    const container = reset('price-grid');
    const filter = el('ticker-filter').value.trim().toUpperCase();
    const all = rows(section('prices'));
    const prices = all.filter(p => String(p.ticker || '').toUpperCase().includes(filter));
    const techs = new Map(rows(section('technicals')).map(t => [t.ticker, t]));
    el('ticker-count').textContent = `${prices.length} / ${all.length} records`;
    if (!prices.length) empty(container, 'No matching price records.');
    for (const p of prices.slice(0, 200)) {
        const t = techs.get(p.ticker) || {};
        const available = finite(p.price);
        const up = finite(p.change_pct) && p.change_pct > 0;
        const down = finite(p.change_pct) && p.change_pct < 0;
        const item = node('div', `price-card ${up ? 'positive' : down ? 'negative' : 'neutral'}`);
        item.append(node('div', 'ticker', p.ticker), node('div', 'price', number(p.price)),
                    node('div', `change ${up ? 'up' : down ? 'down' : 'flat'}`, `${percent(p.change_pct)} vs session open`));
        const rsiValid = finite(t.rsi) && t.rsi >= 0 && t.rsi <= 100;
        if (rsiValid) {
            const bar = node('div', 'rsi-bar'); const fill = node('div', 'rsi-fill');
            fill.style.width = `${t.rsi}%`; bar.append(fill); item.append(bar);
        }
        item.append(node('div', 'rsi-label', `RSI ${rsiValid ? number(t.rsi, 1) : 'Unavailable'} · ${text(t.trend)}`),
                    node('div', 'record-meta', `Source session ${text(p.source_session)} · ${p.session_complete === true ? 'Completed' : p.session_complete === false ? 'Incomplete' : 'Completion unknown'}`),
                    node('div', 'record-meta', `As of ${text(p.as_of)} · Units ${text(p.currency || (String(p.ticker).startsWith('^') ? 'index points' : 'provider quote units'))}`));
        if (!available || t.error) item.append(node('div', 'record-meta', t.error || 'Price unavailable; inspect source coverage.'));
        container.append(item);
    }
}
function renderTechAlerts() {
    const filter = el('ticker-filter').value.trim().toUpperCase();
    const items = rows(section('technicals')).filter(t => String(t.ticker || '').toUpperCase().includes(filter))
        .flatMap(t => (Array.isArray(t.alerts) ? t.alerts : []).map(alert => ({...t, alert})));
    el('alert-count').textContent = items.length;
    boundedList('tech-alerts-list', items, (container, t) => card(container, t.ticker,
        `${text(t.alert)} · ${meta(t)} · Baseline n=${text(t.volume_ratio_baseline_n)}`));
}
function renderSectorRotation() {
    const container = reset('sector-rotation'); const s = object(section('sector_rotation'));
    kv(container, 'Descriptive basket label', finite(s.spread_5d) ? text(s.signal) : 'UNAVAILABLE');
    kv(container, 'Growth: 5 sessions', percent(s.growth_5d));
    kv(container, 'Defensive: 5 sessions', percent(s.defensive_5d));
    kv(container, 'Defensive minus growth', finite(s.spread_5d) ? `${number(s.spread_5d)} percentage points` : 'Unavailable');
    kv(container, '20-session spread', finite(s.spread_20d) ? `${number(s.spread_20d)} percentage points` : 'Unavailable');
    if (s.coverage) container.append(node('pre', 'source-json', JSON.stringify(s.coverage, null, 2)));
    container.append(node('div', 'record-meta', s.interpretation || 'Legacy snapshot: inspect version-specific return definitions.'));
}
function renderOptionAnomalies() {
    const items = Object.values(object(section('options_flow'))).flatMap(d => rows(d?.option_contract_volume_oi_anomaly));
    el('option-anomaly-count').textContent = items.length;
    boundedList('option-anomaly-list', items, (container, s) => card(container,
        `${text(s.ticker)} · ${text(s.type)} · strike ${number(s.strike)}`,
        `DTE ${text(s.dte)} · Vol/OI ${number(s.vol_oi_ratio)} · Estimated notional ${number(s.notional_estimate ?? s.premium)} · ${meta(s)}. Not classified order flow.`));
}
function renderInstrumentSpreads() {
    const spread = object(section('instrument_relative_return_spread'));
    boundedList('instrument-spread-list', rows(spread.pairs), (container, p) => card(container,
        `${text(p.commodity)} · ${p.threshold_crossed === true ? 'Threshold crossed' : 'Below configured threshold'}`,
        `${text(p.physical_ticker)} ${percent(p.physical_5d_chg)} · ${text(p.paper_ticker)} ${percent(p.paper_5d_chg)} · Difference ${number(p.spread)} percentage points · ${text(p.start_session)} to ${text(p.end_session)}`));
    rows(spread.excluded_pairs).forEach(p => card(el('instrument-spread-list'), `${text(p.physical_ticker)} / ${text(p.paper_ticker)} unavailable`,
        `${text(p.physical_reason)} · ${text(p.paper_reason)}`, 'warning'));
}
function renderContrarian() {
    const data = object(section('retail_contrarian'));
    boundedList('contrarian-list', rows(data.subreddits), (container, s) => card(container,
        `r/${text(s.subreddit)} · ${s.is_topped === true ? 'Elevated language intensity' : 'Below configured threshold'}`,
        `${finite(s.euphoria_ratio) ? number(s.euphoria_ratio * 100, 0) : 'Unknown'}% keyword-labelled · ${text(s.euphoric_posts)} / ${text(s.total_posts)} posts. Not an investor sentiment census.`));
    const biz = rows(data.biz);
    if (biz.length) card(el('contrarian-list'), `${biz.length} /biz/ records`, biz.slice(0, 3).map(b => text(b.subject || b.comment).slice(0, 100)).join(' · '));
}
function renderOptionsFlow() {
    const items = Object.entries(object(section('options_flow'))).flatMap(([ticker, d]) =>
        (Array.isArray(d?.alerts) ? d.alerts : []).map(alert => ({ticker, alert, ...d})));
    boundedList('options-list', items, (container, d) => card(container, d.ticker,
        `${text(d.alert)} · Baseline n=${text(d.put_call_vol_baseline_n)} · ${meta(d)}`));
}
function renderLinked(id, records, titleFn, descriptionFn) {
    boundedList(id, records, (container, record) => {
        const item = node('div', 'text-item');
        appendTag(item, 'source-tag', record.publisher || record.source || record.provider || 'Unknown source');
        item.append(node('div', '', titleFn(record)), node('div', 'record-meta', descriptionFn(record)));
        appendExternalLink(item, record.canonical_url || record.link || record.url, 'Open source'); container.append(item);
    });
}
function renderHeadlines() { renderLinked('headlines-list', rows(section('headlines')), d => d.title || d.text,
    d => `${text(d.lane)} · Tickers ${text(d.universe_tickers)} · ${meta(d)}`); }
function renderSECFilings() { renderLinked('sec-list', rows(section('sec_filings')), d => `${text(d.ticker)} · ${text(d.form_type)} · ${text(d.description)}`, meta); }
function renderClinical() {
    const items = rows(section('clinical_catalysts')); el('clinical-count').textContent = items.length;
    renderLinked('clinical-list', items, d => d.title, d => `${text(d.sponsor)} · ${text(d.status)} · ${text(d.phase)} · Date ${text(d.target_date)} · Registry context, not a clinical outcome. ${meta(d)}`);
}
function renderCash(id, items, countId) {
    el(countId).textContent = items.length;
    boundedList(id, items, (container, r) => card(container, `${text(r.ticker)} · ${text(r.risk_level)}`,
        `Runway ${number(r.runway_quarters)} quarters · Cash ${number(r.cash_musd)} USD million · Burn ${number(r.burn_musd)} USD million/quarter · Cash-flow positive: ${text(r.cash_flow_positive)} · ${meta(r)}`,
        r.risk_level === 'RED' ? 'danger' : r.risk_level === 'YELLOW' ? 'warning' : 'info'));
}
function renderCashRunway() { renderCash('cash-runway-list', rows(section('cash_runway_alerts')), 'cash-runway-count'); }
function renderCashMonitor() { renderCash('cash-monitor-list', rows(section('cash_runway')), 'cash-monitor-count'); }
function renderCEO() { renderLinked('ceo-list', rows(section('ceo_ca_signals')), d => d.title,
    d => `${d.source_record_verified === true ? 'Direct source record' : text(d.provenance_status)} · ${d.threshold_qualified === true ? 'Geology threshold met' : 'Context only'} · ${text(d.grade_tag)} · ${meta(d)}`); }
function renderWhispers() { renderLinked('whispers-list', rows(section('social_whispers')), d => d.text, meta); }
function renderTwitter() { renderLinked('twitter-list', rows(section('twitter_signals')), d => `@${text(d.account)} · ${text(d.title)}`, meta); }
function renderInsiders() {
    boundedList('insider-list', rows(section('insider_clusters')), (container, c) => card(container,
        `${text(c.ticker)} · ${text(c.cluster_direction)} cluster`,
        `${text(c.insider_count)} distinct insiders · ${text(c.filing_count)} filings · ${text(c.period_days)} days · ${meta(c)}. Transaction direction is not a recommendation.`));
}
async function copyRawDump() {
    if (!RAW_TEXT) { el('load-status').textContent = 'No export loaded to copy.'; return; }
    try { await navigator.clipboard.writeText(RAW_TEXT); el('load-status').textContent = 'Export copied.'; }
    catch (_error) {
        const range = document.createRange(); range.selectNodeContents(el('raw-dump'));
        const selected = window.getSelection(); selected.removeAllRanges(); selected.addRange(range);
        el('load-status').textContent = 'Clipboard permission unavailable. Raw export selected for manual copy.';
    }
}
document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => { t.classList.remove('active'); t.setAttribute('aria-pressed', 'false'); });
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active'); tab.setAttribute('aria-pressed', 'true'); el(`panel-${tab.dataset.tab}`).classList.add('active');
    }));
    ['copy-all-btn', 'fab-copy'].forEach(id => el(id).addEventListener('click', copyRawDump));
    el('refresh-btn').addEventListener('click', loadData);
    el('brief-file').addEventListener('change', event => loadLocalFile(event.target.files[0]));
    const drop = el('brief-drop');
    drop.addEventListener('dragover', event => event.preventDefault());
    drop.addEventListener('drop', event => { event.preventDefault(); loadLocalFile(event.dataTransfer.files[0]); });
    el('ticker-filter').addEventListener('input', () => { if (DATA) { renderPrices(); renderTechAlerts(); } });
    await loadData();
});
