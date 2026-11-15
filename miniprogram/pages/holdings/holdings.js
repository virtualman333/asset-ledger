const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        loading: true,
        error: '',
        summary: null,
        positions: [],
        baseCurrency: 'CNY',
        totalAssets: '—',
        marketValue: '—',
        unrealized: '—',
        unrealizedTrend: '',
        realized: '—',
        realizedTrend: '',
        dividendTotal: '—',
        annualized: '—',
        valuationNote: '',
        fxWarning: false,
        refreshing: false,
    },

    onLoad() {
        if (!api.access()) {
            wx.reLaunch({ url: '/pages/index/index' });
            return;
        }
    },

    onShow() {
        if (!api.access()) return;
        this.load();
    },

    onPullDownRefresh() {
        const that = this;
        this.load().then(function () { wx.stopPullDownRefresh(); });
    },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        return Promise.all([
            api.request({ path: '/analytics/summary/' }),
            api.request({ path: '/analytics/positions/' }),
        ]).then(function (values) {
            const summary = values[0];
            const positions = (values[1] && values[1].results) || [];
            const rows = positions.map(function (p) {
                return {
                    id: p.asset_id,
                    accountId: p.account_id,
                    symbol: p.symbol,
                    name: p.name || p.symbol,
                    accountName: p.account_name,
                    marketLabel: f.marketLabel(p.market),
                    quantity: f.num(p.quantity, 4),
                    avgCost: f.money(p.avg_cost, 4),
                    lastPrice: f.money(p.last_price, 4),
                    marketValue: f.money(p.market_value),
                    costBasis: f.money(p.cost_basis),
                    pnl: f.money(p.unrealized_pnl),
                    pnlTrend: f.trend(p.unrealized_pnl),
                    dividend: f.money(p.annual_dividend),
                    yield: f.pct(p.dividend_yield),
                };
            });
            const total = (Number(summary.market_value) || 0) + (Number(summary.unpriced_cost_basis) || 0);
            that.setData({
                loading: false,
                summary: summary,
                positions: rows,
                baseCurrency: summary.base_currency || 'CNY',
                totalAssets: f.money(total),
                marketValue: f.money(summary.market_value),
                unrealized: f.money(summary.unrealized_pnl),
                unrealizedTrend: f.trend(summary.unrealized_pnl),
                realized: f.money(summary.realized_pnl),
                realizedTrend: f.trend(summary.realized_pnl),
                dividendTotal: f.money(summary.dividend_total),
                annualized: f.pct(summary.annualized),
                valuationNote: summary.valuation_note || '',
                fxWarning: !!summary.fx_warning,
            });
        }).catch(function (err) {
            that.setData({ loading: false, error: err.message });
        });
    },

    refreshQuotes() {
        const that = this;
        if (!this.data.positions.length || this.data.refreshing) return;
        const ids = this.data.positions.map(function (p) { return p.id; }).join(',');
        this.setData({ refreshing: true });
        api.request({ path: '/market/quotes/?asset_ids=' + ids + '&refresh=1' })
            .then(function () { wx.showToast({ title: '行情已刷新' }); })
            .catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); })
            .then(function () { that.setData({ refreshing: false }); that.load(); });
    },

    goRecord() { wx.switchTab({ url: '/pages/record/record' }); },

    openPosition(event) {
        const id = event.currentTarget.dataset.id;
        wx.navigateTo({ url: '/pages/records/records?asset=' + id });
    },
});
