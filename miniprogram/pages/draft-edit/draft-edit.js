const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        id: null,
        draft: null,
        sides: f.SIDES,
        sideLabels: f.SIDE_LABELS,
        sideIndex: -1,
        currencies: f.CURRENCIES,
        currencyIndex: 0,
        accounts: [],
        accountNames: [],
        accountIndex: 0,
        overrides: { symbol: '', side: '', quantity: '', price: '', amount: '', currency: '', fee: '', tax: '' },
        loading: true,
        error: '',
        submitting: false,
    },

    onLoad(options) {
        if (!api.access()) { wx.reLaunch({ url: '/pages/index/index' }); return; }
        this.setData({ id: Number(options.id) });
        this.load();
    },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        Promise.all([
            api.request({ path: '/ingest/jobs/' + this.data.id + '/' }),
            api.request({ path: '/accounts/' }),
        ]).then(function (values) {
            const job = values[0];
            const result = job.result || {};
            const sideIndex = that.data.sides.indexOf(result.side);
            const currencyIndex = Math.max(0, that.data.currencies.indexOf(result.currency || 'CNY'));
            const accounts = api.pageResults(values[1]);
            const nameHint = result.account_hint || '';
            let accountIndex = 0;
            accounts.forEach(function (a, i) { if (nameHint && a.name.indexOf(nameHint) >= 0) accountIndex = i; });
            that.setData({
                draft: {
                    kind: job.kind === 'IMAGE' ? '截图' : '文本',
                    confidence: f.pct(job.confidence, 0),
                    missing: job.missing || [],
                    rawText: job.raw_text || '',
                    error: job.error || '',
                    evidenceUrl: job.evidence_url || '',
                },
                sideIndex: sideIndex,
                currencyIndex: currencyIndex,
                accounts: accounts,
                accountNames: accounts.map(function (a) { return a.name; }),
                accountIndex: accountIndex,
                overrides: {
                    symbol: result.symbol || '',
                    side: result.side || '',
                    quantity: result.quantity === undefined || result.quantity === null ? '' : String(result.quantity),
                    price: result.price === undefined || result.price === null ? '' : String(result.price),
                    amount: result.amount === undefined || result.amount === null ? '' : String(result.amount),
                    currency: result.currency || 'CNY',
                    fee: result.fee === undefined || result.fee === null ? '' : String(result.fee),
                    tax: result.tax === undefined || result.tax === null ? '' : String(result.tax),
                },
                loading: false,
            });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },

    onFieldInput(event) {
        this.setData({ ['overrides.' + event.currentTarget.dataset.field]: event.detail.value });
    },
    onSideChange(event) {
        const index = Number(event.detail.value);
        this.setData({ sideIndex: index, 'overrides.side': this.data.sides[index] });
    },
    onCurrencyChange(event) {
        const index = Number(event.detail.value);
        this.setData({ currencyIndex: index, 'overrides.currency': this.data.currencies[index] });
    },
    onAccountChange(event) { this.setData({ accountIndex: Number(event.detail.value) }); },

    cancel() { wx.navigateBack(); },

    confirm() {
        const that = this;
        const o = this.data.overrides;
        if (!o.symbol.trim()) { this.setData({ error: '请补充标的代码' }); return; }
        this.setData({ submitting: true, error: '' });
        const overrides = {
            symbol: o.symbol.trim(),
            side: o.side || this.data.sides[Math.max(0, this.data.sideIndex)],
            currency: o.currency,
        };
        ['quantity', 'price', 'amount', 'fee', 'tax'].forEach(function (key) {
            if (o[key] !== '') overrides[key] = Number(o[key]);
        });
        const payload = { overrides: overrides };
        if (this.data.accounts.length) payload.account_id = this.data.accounts[this.data.accountIndex].id;
        api.request({ path: '/ingest/drafts/' + this.data.id + '/confirm/', method: 'POST', data: payload })
            .then(function () {
                wx.showToast({ title: '已入账', icon: 'success' });
                setTimeout(function () { wx.navigateBack(); }, 500);
            }).catch(function (err) {
                that.setData({ submitting: false, error: err.message });
            });
    },
});
