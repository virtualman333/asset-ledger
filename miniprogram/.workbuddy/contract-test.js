// 临时契约测试：用线上真实响应形状驱动页面 JS，验证字段映射。
// 只用于本次开发自测，不随小程序打包（project.config.json 已忽略 .workbuddy）。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const responses = JSON.parse(fs.readFileSync(path.join(__dirname, 'api-samples.json'), 'utf8'));

const routeMap = {
    '/analytics/summary/': responses.summary,
    '/analytics/positions/': responses.positions,
    '/analytics/dividends/?year=2026': responses.dividends,
    '/analytics/calendar/?year=2026': responses.calendar,
    '/accounts/': responses.accounts,
    '/accounts/?page_size=200': responses.accounts,
    '/assets/?ordering=symbol': responses.assets,
    '/assets/?search=600519': responses.assetsSearch,
    '/transactions/records/?ordering=-traded_at': responses.records,
    '/transactions/dividends/?year=2026': responses.dividendRecords,
    '/ingest/drafts/': responses.drafts,
    '/market/quotes/': responses.quotes,
    '/market/fx/?base=USD&quote=CNY': responses.fx,
};

let failures = [];
function assert(cond, label) {
    if (cond) console.log('PASS ' + label);
    else { console.log('FAIL ' + label); failures.push(label); }
}

function makeApiStub() {
    return {
        baseUrl() { return 'https://fiance.virtualman.top/api/v1'; },
        access() { return 'test-token'; },
        setSession() {}, clearSession() {},
        setBaseUrl() {},
        request(opts) {
            const key = Object.keys(routeMap).find(function (k) { return opts.path.indexOf(k) === 0; });
            if (opts.path.indexOf('/market/quotes/') === 0) { return Promise.resolve(responses.quotes); }
            if (!key) { return Promise.reject(new Error('unmocked path ' + opts.path)); }
            return Promise.resolve(routeMap[key]);
        },
        pageResults(data) { return data && data.results ? data.results : []; },
        login() { return Promise.resolve({ access: 'x', refresh: 'y' }); },
        register() { return Promise.resolve({}); },
        uploadImage() { return Promise.resolve({ status: 'SUCCEEDED' }); },
        downloadFile() { return Promise.resolve('/tmp/export.csv'); },
        errorMessage(err) { return String(err); },
    };
}

function makeWxStub() {
    return {
        getStorageSync() { return ''; },
        setStorageSync() {},
        removeStorageSync() {},
        showToast() {}, showModal() {},
        navigateTo() {}, switchTab() {}, reLaunch() {}, navigateBack() {},
        request() {},
        uploadFile() {},
        chooseMedia() {}, chooseMessageFile() {}, previewImage() {}, shareFileMessage() {},
        getFileSystemManager() { return { readFile() {}, writeFileSync() {} }; },
        get env() { return { USER_DATA_PATH: '/tmp' }; },
        stopPullDownRefresh() {},
    };
}

function loadPage(relPath, overrides) {
    const file = path.join(ROOT, relPath);
    const code = fs.readFileSync(file, 'utf8');
    let pageDef = null;
    const sandbox = {
        module: { exports: {} }, exports: {},
        require(name) {
            if (name.indexOf('utils/api') >= 0) return makeApiStub();
            if (name.indexOf('utils/format') >= 0) return require(path.join(ROOT, 'utils/format.js'));
            return require(name);
        },
        getApp() { return { globalData: {} }; },
        wx: makeWxStub(),
        console: console,
        Date: Date, Math: Math, Number: Number, String: String, Object: Object, Array: Array,
        Promise: Promise, encodeURIComponent: encodeURIComponent, JSON: JSON, setTimeout: setTimeout,
        Page(def) { pageDef = def; },
        App() {},
    };
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox, { filename: relPath });
    if (!pageDef) throw new Error('no Page() in ' + relPath);
    const page = Object.assign({}, pageDef, overrides || {});
    page.data = JSON.parse(JSON.stringify(pageDef.data));
    page.setData = function (patch, cb) {
        Object.keys(patch).forEach(function (key) { page.data[key] = patch[key]; });
        if (typeof cb === 'function') cb();
    };
    return page;
}

async function testHoldings() {
    const page = loadPage('pages/holdings/holdings.js');
    await page.load();
    assert(page.data.totalAssets !== '—', 'holdings: totalAssets rendered');
    assert(page.data.positions.length === 3, 'holdings: 3 positions');
    assert(page.data.positions[0].symbol === '600519', 'holdings: symbol mapped');
    assert(page.data.positions[0].pnlTrend === 'down', 'holdings: pnl color down (red/green convention)');
    assert(page.data.dividendTotal === '800.00', 'holdings: dividend total formatted');
    assert(page.data.annualized === '—', 'holdings: null annualized shows dash');
    assert(page.data.fxWarning === false, 'holdings: fx warning flag');
}

async function testStats() {
    const page = loadPage('pages/stats/stats.js');
    await page.load();
    assert(page.data.months.length > 0, 'stats: dividend months mapped');
    assert(page.data.costBasis !== '—', 'stats: cost basis formatted');
    assert(page.data.upcoming.length === 0 && page.data.received.length === 1, 'stats: calendar groups mapped');
    assert(page.data.nextPayDate === '—', 'stats: null next pay date shows dash');
}

async function testInbox() {
    const page = loadPage('pages/inbox/inbox.js');
    await page.load();
    assert(page.data.drafts.length === 0, 'inbox: drafts empty handled');
}

async function testRecords() {
    const page = loadPage('pages/records/records.js');
    await page.loadNames();
    await page.load();
    assert(page.data.records.length === 5, 'records: 5 records mapped');
    assert(page.data.records[1].symbol === '600519', 'records: asset id resolved to symbol');
    assert(page.data.records[0].accountName === '华泰证券', 'records: account id resolved to name');
    assert(page.data.records[1].trend === 'down', 'records: buy cash flow colored down');
}

async function testDividends() {
    const page = loadPage('pages/dividends/dividends.js');
    await page.loadOptions();
    await page.load();
    assert(page.data.records.length === 1, 'dividends: one record mapped');
    assert(page.data.records[0].symbol === '600519', 'dividends: symbol resolved');
    assert(page.data.records[0].net === '800.00', 'dividends: net formatted');
}

async function testAccountAssetEdit() {
    const accounts = loadPage('pages/accounts/accounts.js');
    await accounts.load();
    accounts.edit({ currentTarget: { dataset: { id: 1 } } });
    assert(accounts.data.form.market === 'A' && accounts.data.form.account_type === 'BROKER', 'accounts: edit keeps raw enums');
    assert(accounts.data.typeIndex === 0 && accounts.data.marketIndex === 0, 'accounts: edit picker indexes');

    const assets = loadPage('pages/assets/assets.js');
    await assets.load();
    assets.edit({ currentTarget: { dataset: { id: 1 } } });
    assert(assets.data.form.market === 'A' && assets.data.form.asset_type === 'STOCK', 'assets: edit keeps raw enums');
    assert(assets.data.typeIndex === 0, 'assets: edit picker index');
}

async function testRecordPreview() {
    const page = loadPage('pages/record/record.js');
    page.applySide(0);
    page.onFieldInput({ currentTarget: { dataset: { field: 'quantity' } }, detail: { value: '10' } });
    page.onFieldInput({ currentTarget: { dataset: { field: 'price' } }, detail: { value: '100' } });
    assert(page.data.preview.indexOf('-1,000.00') >= 0 || page.data.preview.indexOf('-1000.00') >= 0, 'record: BUY preview computes negative cash flow');
}

async function testQuotes() {
    const page = loadPage('pages/quotes/quotes.js');
    await page.load(false);
    assert(page.data.quotes.length === 3, 'quotes: quote rows mapped');
    assert(page.data.quotes[0].price.replace(/,/g, '') === '1435.2600', 'quotes: price formatted');
}

(async function main() {
    const tasks = [testHoldings, testStats, testInbox, testRecords, testDividends, testAccountAssetEdit, testRecordPreview, testQuotes];
    for (const task of tasks) {
        try { await task(); }
        catch (e) { failures.push(task.name + ': ' + e.message); console.log('FAIL ' + task.name + ': ' + e.message); }
    }
    console.log('\n结果：' + (failures.length ? failures.length + ' 条失败' : '全部通过'));
    process.exit(failures.length ? 1 : 0);
})();
