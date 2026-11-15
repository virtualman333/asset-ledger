const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        assets: [],
        keyword: '',
        loading: true,
        error: '',
        editing: false,
        form: { id: null, symbol: '', name: '', market: 'A', asset_type: 'STOCK', currency: 'CNY', is_dividend_asset: false, note: '' },
        markets: f.MARKETS,
        marketLabels: f.MARKET_LABELS,
        marketIndex: 0,
        types: f.ASSET_TYPES,
        typeLabels: f.ASSET_TYPE_LABELS,
        typeIndex: 0,
        currencies: f.CURRENCIES,
        currencyIndex: 0,
    },

    onLoad() { if (!api.access()) wx.reLaunch({ url: '/pages/index/index' }); },
    onShow() { if (api.access()) this.load(); },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        const path = '/assets/?' + (this.data.keyword ? 'search=' + encodeURIComponent(this.data.keyword) + '&' : '') + 'ordering=symbol';
        api.request({ path: path }).then(function (data) {
            const rows = api.pageResults(data).map(function (a) {
                return {
                    id: a.id, symbol: a.symbol, name: a.name || '',
                    market: a.market,
                    assetType: a.asset_type,
                    marketLabel: f.marketLabel(a.market),
                    typeLabel: f.assetTypeLabel(a.asset_type),
                    currency: a.currency,
                    dividend: a.is_dividend_asset ? '股息标的' : '',
                };
            });
            that.setData({ assets: rows, loading: false });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },

    onKeywordInput(event) { this.setData({ keyword: event.detail.value }); },
    search() { this.load(); },

    add() {
        this.setData({
            editing: true,
            form: { id: null, symbol: '', name: '', market: 'A', asset_type: 'STOCK', currency: 'CNY', is_dividend_asset: false, note: '' },
            marketIndex: 0, typeIndex: 0, currencyIndex: 0, error: '',
        });
    },

    edit(event) {
        const id = event.currentTarget.dataset.id;
        const a = this.data.assets.filter(function (x) { return x.id === id; })[0];
        if (!a) return;
        this.setData({
            editing: true,
            form: {
                id: a.id, symbol: a.symbol, name: a.name,
                market: a.market,
                asset_type: a.assetType,
                currency: a.currency, is_dividend_asset: a.dividend === '股息标的', note: a.note || '',
            },
            marketIndex: Math.max(0, f.MARKETS.indexOf(a.market)),
            typeIndex: Math.max(0, f.ASSET_TYPES.indexOf(a.assetType)),
            currencyIndex: Math.max(0, f.CURRENCIES.indexOf(a.currency)),
            error: '',
        });
    },

    onFieldInput(event) { this.setData({ ['form.' + event.currentTarget.dataset.field]: event.detail.value }); },
    onMarketChange(event) {
        const index = Number(event.detail.value);
        this.setData({ marketIndex: index, 'form.market': this.data.markets[index] });
    },
    onTypeChange(event) {
        const index = Number(event.detail.value);
        this.setData({ typeIndex: index, 'form.asset_type': this.data.types[index] });
    },
    onCurrencyChange(event) {
        const index = Number(event.detail.value);
        this.setData({ currencyIndex: index, 'form.currency': this.data.currencies[index] });
    },
    onDividendToggle(event) { this.setData({ 'form.is_dividend_asset': event.detail.value }); },
    cancel() { this.setData({ editing: false }); },

    save() {
        const that = this;
        const form = this.data.form;
        if (!form.symbol.trim()) { this.setData({ error: '请填写标的代码' }); return; }
        const payload = {
            symbol: form.symbol.trim(), name: form.name, market: form.market,
            asset_type: form.asset_type, currency: form.currency,
            is_dividend_asset: form.is_dividend_asset, note: form.note,
        };
        const isEdit = !!form.id;
        api.request({
            path: isEdit ? '/assets/' + form.id + '/' : '/assets/',
            method: isEdit ? 'PATCH' : 'POST',
            data: payload,
        }).then(function () { that.setData({ editing: false }); wx.showToast({ title: '已保存' }); that.load(); })
            .catch(function (err) { that.setData({ error: err.message }); });
    },

    remove(event) {
        const that = this;
        const id = event.currentTarget.dataset.id;
        wx.showModal({
            title: '删除标的',
            content: '已有流水的标的不能删除。确定继续？',
            success(res) {
                if (!res.confirm) return;
                api.request({ path: '/assets/' + id + '/', method: 'DELETE' })
                    .then(function () { wx.showToast({ title: '已删除' }); that.load(); })
                    .catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
            },
        });
    },
});
