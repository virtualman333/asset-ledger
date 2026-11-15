const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        quotes: [],
        loading: true,
        refreshing: false,
        error: '',
        fxBase: 'USD',
        fxQuote: 'CNY',
        fxResult: '',
        currencies: f.CURRENCIES,
        baseIndex: 0,
        quoteIndex: 0,
    },

    onLoad() { if (!api.access()) wx.reLaunch({ url: '/pages/index/index' }); },
    onShow() { if (api.access()) this.load(false); },

    load(refresh) {
        const that = this;
        this.setData({ loading: true, error: '' });
        return api.request({ path: '/analytics/positions/' }).then(function (data) {
            const ids = (data.results || []).map(function (p) { return p.asset_id; });
            if (ids.length === 0) {
                that.setData({ loading: false, quotes: [] });
                return null;
            }
            return api.request({ path: '/market/quotes/?asset_ids=' + ids.join(',') + (refresh ? '&refresh=1' : '') })
                .then(function (q) {
                    const rows = (q.results || []).map(function (item) {
                        return {
                            id: item.asset_id,
                            symbol: item.symbol,
                            name: item.name || '',
                            price: f.money(item.price, 4),
                            change: item.change_pct === undefined ? '—' : f.pct(item.change_pct),
                            changeTrend: f.trend(item.change_pct),
                            currency: item.currency || '',
                            fetchedAt: f.datetime(item.fetched_at),
                            source: item.source || '',
                            stale: item.stale ? '旧价' : '',
                        };
                    });
                    that.setData({ quotes: rows, loading: false });
                });
        }).then(function () {
            if (refresh) wx.showToast({ title: '行情已刷新' });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },

    refresh() {
        if (this.data.refreshing) return;
        const that = this;
        this.setData({ refreshing: true });
        this.load(true).then(function () { that.setData({ refreshing: false }); });
    },

    onBaseChange(event) {
        const index = Number(event.detail.value);
        this.setData({ baseIndex: index, fxBase: this.data.currencies[index] });
    },
    onQuoteChange(event) {
        const index = Number(event.detail.value);
        this.setData({ quoteIndex: index, fxQuote: this.data.currencies[index] });
    },

    loadFx() {
        const that = this;
        this.setData({ fxResult: '', error: '' });
        api.request({ path: '/market/fx/?base=' + this.data.fxBase + '&quote=' + this.data.fxQuote })
            .then(function (data) {
                that.setData({ fxResult: '1 ' + data.base + ' = ' + f.num(data.rate, 6) + ' ' + data.quote + '（' + (data.source || '—') + (data.stale ? ' · 旧汇率' : '') + '）' });
            }).catch(function (err) { that.setData({ error: err.message }); });
    },
});
