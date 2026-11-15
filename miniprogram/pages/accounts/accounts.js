const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        accounts: [],
        loading: true,
        error: '',
        editing: false,
        form: { id: null, name: '', broker: '', market: 'A', account_type: 'BROKER', currency: 'CNY', note: '' },
        markets: f.MARKETS,
        marketLabels: f.MARKET_LABELS,
        marketIndex: 0,
        types: f.ACCOUNT_TYPES,
        typeLabels: f.ACCOUNT_TYPE_LABELS,
        typeIndex: 0,
        currencies: f.CURRENCIES,
        currencyIndex: 0,
    },

    onLoad() { if (!api.access()) wx.reLaunch({ url: '/pages/index/index' }); },
    onShow() { if (api.access()) this.load(); },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        api.request({ path: '/accounts/' }).then(function (data) {
            const rows = api.pageResults(data).map(function (a) {
                return {
                    id: a.id, name: a.name, broker: a.broker || '',
                    id: a.id,
                    market: a.market,
                    accountType: a.account_type,
                    marketLabel: f.marketLabel(a.market),
                    typeLabel: f.accountTypeLabel(a.account_type),
                    currency: a.currency, note: a.note || '',
                };
            });
            that.setData({ accounts: rows, loading: false });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },

    add() {
        this.setData({
            editing: true,
            form: { id: null, name: '', broker: '', market: 'A', account_type: 'BROKER', currency: 'CNY', note: '' },
            marketIndex: 0, typeIndex: 0, currencyIndex: 0, error: '',
        });
    },

    edit(event) {
        const id = event.currentTarget.dataset.id;
        const a = this.data.accounts.filter(function (x) { return x.id === id; })[0];
        if (!a) return;
        this.setData({
            editing: true,
            form: {
                id: a.id, name: a.name, broker: a.broker,
                market: a.market,
                account_type: a.accountType,
                currency: a.currency, note: a.note,
            },
            marketIndex: Math.max(0, f.MARKETS.indexOf(a.market)),
            typeIndex: Math.max(0, f.ACCOUNT_TYPES.indexOf(a.accountType)),
            currencyIndex: Math.max(0, f.CURRENCIES.indexOf(a.currency)),
            error: '',
        });
    },

    onFieldInput(event) {
        this.setData({ ['form.' + event.currentTarget.dataset.field]: event.detail.value });
    },
    onMarketChange(event) {
        const index = Number(event.detail.value);
        this.setData({ marketIndex: index, 'form.market': this.data.markets[index] });
    },
    onTypeChange(event) {
        const index = Number(event.detail.value);
        this.setData({ typeIndex: index, 'form.account_type': this.data.types[index] });
    },
    onCurrencyChange(event) {
        const index = Number(event.detail.value);
        this.setData({ currencyIndex: index, 'form.currency': this.data.currencies[index] });
    },
    cancel() { this.setData({ editing: false }); },

    save() {
        const that = this;
        const form = this.data.form;
        if (!form.name.trim()) { this.setData({ error: '请填写账户名称' }); return; }
        const payload = {
            name: form.name.trim(), broker: form.broker, market: form.market,
            account_type: form.account_type, currency: form.currency, note: form.note,
        };
        const isEdit = !!form.id;
        const path = isEdit ? '/accounts/' + form.id + '/' : '/accounts/';
        api.request({ path: path, method: isEdit ? 'PATCH' : 'POST', data: payload })
            .then(function () { that.setData({ editing: false }); wx.showToast({ title: '已保存' }); that.load(); })
            .catch(function (err) { that.setData({ error: err.message }); });
    },

    remove(event) {
        const that = this;
        const id = event.currentTarget.dataset.id;
        wx.showModal({
            title: '删除账户',
            content: '已有流水的账户不能删除。确定继续？',
            success(res) {
                if (!res.confirm) return;
                api.request({ path: '/accounts/' + id + '/', method: 'DELETE' })
                    .then(function () { wx.showToast({ title: '已删除' }); that.load(); })
                    .catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
            },
        });
    },
});
