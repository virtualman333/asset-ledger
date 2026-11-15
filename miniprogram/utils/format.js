// 展示层格式化；数字口径全部来自后端，不做第二份业务计算。
function num(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (isNaN(n)) return '—';
    const d = digits === undefined ? 2 : digits;
    return n.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function money(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    return num(value, digits);
}

function pct(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (isNaN(n)) return '—';
    return num(n * 100, digits === undefined ? 2 : digits) + '%';
}

function trend(value) {
    const n = Number(value);
    if (isNaN(n) || n === 0) return '';
    // A股习惯：红涨绿跌
    return n > 0 ? 'up' : 'down';
}

function date(value) {
    if (!value) return '—';
    return String(value).slice(0, 10);
}

function datetime(value) {
    if (!value) return '—';
    return String(value).slice(0, 16).replace('T', ' ');
}

function today() {
    const d = new Date();
    const m = d.getMonth() + 1;
    const day = d.getDate();
    return d.getFullYear() + '-' + (m < 10 ? '0' + m : m) + '-' + (day < 10 ? '0' + day : day);
}

function localDateTime() {
    const d = new Date();
    const pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return today() + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':00';
}

function year() { return new Date().getFullYear(); }

const SIDES = ['BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW'];
const SIDE_LABELS = ['买入', '卖出', '股息', '入金', '出金'];
const MARKETS = ['A', 'HK', 'US', 'FUND', 'CRYPTO', 'FOREX', 'OTHER'];
const MARKET_LABELS = ['A股', '港股', '美股', '基金', '数字货币', '外汇', '其他'];
const CURRENCIES = ['CNY', 'USD', 'HKD', 'USDT', 'EUR', 'JPY'];
const ACCOUNT_TYPES = ['BROKER', 'BANK', 'CRYPTO', 'CASH', 'OTHER'];
const ACCOUNT_TYPE_LABELS = ['券商', '银行', '交易所/钱包', '现金', '其他'];
const ASSET_TYPES = ['STOCK', 'ETF', 'BOND', 'FUND', 'CRYPTO', 'FOREX', 'CASH', 'OTHER'];
const ASSET_TYPE_LABELS = ['股票', 'ETF', '债券', '基金', '加密', '外汇', '现金', '其他'];

function label(list, labels, value) {
    const i = list.indexOf(value);
    return i < 0 ? (value || '—') : labels[i];
}

function sideLabel(value) { return label(SIDES, SIDE_LABELS, value); }
function marketLabel(value) { return label(MARKETS, MARKET_LABELS, value); }
function accountTypeLabel(value) { return label(ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS, value); }
function assetTypeLabel(value) { return label(ASSET_TYPES, ASSET_TYPE_LABELS, value); }

function currencyForMarket(market) {
    if (market === 'HK') return 'HKD';
    if (market === 'US') return 'USD';
    if (market === 'CRYPTO') return 'USDT';
    return 'CNY';
}

module.exports = {
    num: num,
    money: money,
    pct: pct,
    trend: trend,
    date: date,
    datetime: datetime,
    today: today,
    localDateTime: localDateTime,
    year: year,
    SIDES: SIDES,
    SIDE_LABELS: SIDE_LABELS,
    MARKETS: MARKETS,
    MARKET_LABELS: MARKET_LABELS,
    CURRENCIES: CURRENCIES,
    ACCOUNT_TYPES: ACCOUNT_TYPES,
    ACCOUNT_TYPE_LABELS: ACCOUNT_TYPE_LABELS,
    ASSET_TYPES: ASSET_TYPES,
    ASSET_TYPE_LABELS: ASSET_TYPE_LABELS,
    sideLabel: sideLabel,
    marketLabel: marketLabel,
    accountTypeLabel: accountTypeLabel,
    assetTypeLabel: assetTypeLabel,
    currencyForMarket: currencyForMarket,
};
