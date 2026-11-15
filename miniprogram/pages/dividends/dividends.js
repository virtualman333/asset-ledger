const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        year: f.year(),
        years: [f.year() - 1, f.year(), f.year() + 1],
        yearIndex: 1,
        records: [],
        assets: [],
        assetNames: [],
        assetIndex: 0,
        accounts: [],
        accountNames: [],
        accountIndex: 0,
        loading: true,
        error: '',
        showForm: false,
        form: { pay_date: f.today(), ex_date: '', gross: '', tax: '0', net: '', currency: 'CNY', reinvested: false, note: '' },
        currencies: f.CURRENCIES,
        currencyIndex: 0,
    },

    onLoad() {
        if (!api.access()) { wx.reLaunch({ url: '/pages/index/index' }); return; }
        this.loadOptions();
    },
    onShow() { if (api.access()) this.load(); },

    loadOptions() {
        const that = this;
        Promise.all([
            api.request({ path: '/assets/?ordering=symbol' }),
            api.request({ path: '/accounts/' }),
        ]).then(function (values) {
            const assets = api.pageResults(values[0]);
            const accounts = api.pageResults(values[1]);
            that.setData({
                assets: assets,
                assetNames: assets.map(function (a) { return a.symbol + ' ' + (a.name || ''); }),
                accounts: accounts,
                accountNames: accounts.map(function (a) { return a.name; }),
            });
        }).catch(function () { /* 记录页仍可用：确认时由后端再校验 */ });
    },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        api.request({ path: '/transactions/dividends/?year=' + this.data.year })
            .then(function (data) {
                const rows = api.pageResults(data).map(function (d) {
                    const net = Number(d.net) || 0;
                    const asset = that.data.assets.filter(function (a) { return a.id === d.asset; })[0];
                    const account = that.data.accounts.filter(function (a) { return a.id === d.account; })[0];
                    return {
                        id: d.id,
                        symbol: asset ? asset.symbol : ('标的 #' + d.asset),
                        account: account ? account.name : ('账户 #' + (d.account || 0)),
                        payDate: f.date(d.pay_date),
                        exDate: f.date(d.ex_date),
                        gross: f.money(d.gross),
                        tax: f.money(d.tax),
                        net: f.money(net),
                        currency: d.currency,
                        reinvested: d.reinvested ? '再投资' : '',
                    };
                });
                that.setData({ records: rows, loading: false });
            }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },

    onYearChange(event) {
        const index = Number(event.detail.value);
        this.setData({ yearIndex: index, year: this.data.years[index] }, this.load);
    },
    onAssetChange(event) { this.setData({ assetIndex: Number(event.detail.value) }); },
    onAccountChange(event) { this.setData({ accountIndex: Number(event.detail.value) }); },
    onCurrencyChange(event) {
        const index = Number(event.detail.value);
        this.setData({ currencyIndex: index, 'form.currency': this.data.currencies[index] });
    },
    onFieldInput(event) { this.setData({ ['form.' + event.currentTarget.dataset.field]: event.detail.value }); },
    onDateChange(event) { this.setData({ 'form.pay_date': event.detail.value }); },
    onReinvestToggle(event) { this.setData({ 'form.reinvested': event.detail.value }); },
    showAdd() { this.setData({ showForm: true, error: '' }); },
    hideForm() { this.setData({ showForm: false }); },

    save() {
        const that = this;
        const form = this.data.form;
        if (!this.data.assets.length) { this.setData({ error: '请先到「我的 → 标的库」创建标的' }); return; }
        if (!form.gross && !form.net) { this.setData({ error: '请填写税前或到手金额' }); return; }
        const gross = Number(form.gross) || 0;
        const tax = Number(form.tax) || 0;
        const net = form.net === '' ? gross - tax : Number(form.net);
        const payload = {
            asset: this.data.assets[this.data.assetIndex].id,
            account: this.data.accounts.length ? this.data.accounts[this.data.accountIndex].id : null,
            pay_date: form.pay_date,
            gross: gross, tax: tax, net: net,
            currency: form.currency,
            reinvested: form.reinvested,
        };
        if (form.ex_date) payload.ex_date = form.ex_date;
        api.request({ path: '/transactions/dividends/', method: 'POST', data: payload })
            .then(function () {
                that.setData({ showForm: false, form: { pay_date: f.today(), ex_date: '', gross: '', tax: '0', net: '', currency: 'CNY', reinvested: false } });
                wx.showToast({ title: '已记录' });
                that.load();
            }).catch(function (err) { that.setData({ error: err.message }); });
    },

    remove(event) {
        const that = this;
        const id = event.currentTarget.dataset.id;
        wx.showModal({
            title: '删除股息明细',
            content: '确定删除这条股息记录？',
            success(res) {
                if (!res.confirm) return;
                api.request({ path: '/transactions/dividends/' + id + '/', method: 'DELETE' })
                    .then(function () { wx.showToast({ title: '已删除' }); that.load(); })
                    .catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
            },
        });
    },
});
