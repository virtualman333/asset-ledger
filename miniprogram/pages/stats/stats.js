const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        year: f.year(),
        years: [f.year() - 2, f.year() - 1, f.year(), f.year() + 1],
        yearIndex: 2,
        loading: true,
        error: '',
        summary: null,
        months: [],
        calendar: null,
        baseCurrency: 'CNY',
        costBasis: '—',
        marketValue: '—',
        unrealized: '—',
        realized: '—',
        dividendTotal: '—',
        totalPnl: '—',
        dividendAnnual: '—',
        dividendYield: '—',
        monthlyPassive: '—',
        annualized: '—',
        upcoming: [],
        received: [],
        undated: [],
        nextPayDate: '—',
        valuationNote: '',
    },

    onLoad() {
        if (!api.access()) {
            wx.reLaunch({ url: '/pages/index/index' });
            return;
        }
    },

    onShow() {
        if (api.access()) this.load();
    },

    onPullDownRefresh() {
        const that = this;
        this.load().then(function () { wx.stopPullDownRefresh(); });
    },

    onYearChange(event) {
        const index = Number(event.detail.value);
        this.setData({ yearIndex: index, year: this.data.years[index] });
        this.load();
    },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        const year = this.data.year;
        return Promise.all([
            api.request({ path: '/analytics/summary/' }),
            api.request({ path: '/analytics/dividends/?year=' + year }),
            api.request({ path: '/analytics/calendar/?year=' + year }),
        ]).then(function (values) {
            const summary = values[0];
            const dividend = values[1];
            const calendar = values[2];
            const months = (dividend.months || []).map(function (m) {
                const value = Number(m.net) || 0;
                return {
                    month: m.month,
                    net: f.money(m.net),
                    count: m.count,
                    width: Math.max(4, Math.min(100, Math.abs(value) * 100)),
                };
            });
            const groups = function (list) {
                return (list || []).map(function (item) {
                    return {
                        symbol: item.symbol,
                        assetName: item.asset_name,
                        accountName: item.account_name,
                        amount: f.money(item.amount),
                        currency: item.currency,
                        payDate: f.date(item.pay_date),
                        daysUntil: item.days_until,
                        origin: item.origin === 'transaction' ? '流水录入' : '股息明细',
                    };
                });
            };
            that.setData({
                loading: false,
                summary: summary,
                months: months,
                baseCurrency: summary.base_currency || 'CNY',
                costBasis: f.money(summary.cost_basis),
                marketValue: f.money(summary.market_value),
                unrealized: f.money(summary.unrealized_pnl),
                realized: f.money(summary.realized_pnl),
                dividendTotal: f.money(summary.dividend_total),
                totalPnl: f.money(summary.total_pnl),
                dividendAnnual: f.money(summary.dividend_annual),
                dividendYield: f.pct(summary.dividend_yield),
                monthlyPassive: f.money(summary.monthly_passive_income),
                annualized: f.pct(summary.annualized),
                valuationNote: summary.valuation_note || '',
                calendar: calendar,
                upcoming: groups(calendar.upcoming),
                received: groups(calendar.received),
                undated: groups(calendar.undated),
                nextPayDate: f.date(calendar.totals && calendar.totals.next_pay_date),
            });
        }).catch(function (err) {
            that.setData({ loading: false, error: err.message });
        });
    },

    exportCsv(event) {
        const kind = event.currentTarget.dataset.kind;
        const paths = {
            records: '/transactions/records/export/',
            positions: '/analytics/positions/export/',
            dividends: '/analytics/dividends/export/?year=' + this.data.year,
        };
        api.downloadFile(paths[kind]).then(function (filePath) {
            wx.shareFileMessage({
                filePath: filePath,
                success: function () {},
                fail: function () {
                    wx.showToast({ title: '文件已生成，请在聊天中转发', icon: 'none' });
                },
            });
        }).catch(function (err) {
            wx.showToast({ title: err.message, icon: 'none' });
        });
    },

    goCalendar() { wx.navigateTo({ url: '/pages/calendar/calendar' }); },
    goImport() { wx.navigateTo({ url: '/pages/import/import' }); },
});
