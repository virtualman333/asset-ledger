const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        year: f.year(),
        years: [f.year() - 1, f.year(), f.year() + 1],
        yearIndex: 1,
        loading: true,
        error: '',
        groups: [
            { key: 'upcoming', title: '待派息', list: [] },
            { key: 'received', title: '已到账', list: [] },
            { key: 'undated', title: '日期未定', list: [] },
        ],
        totals: null,
        asOf: '',
    },

    onLoad() { if (!api.access()) wx.reLaunch({ url: '/pages/index/index' }); },
    onShow() { if (api.access()) this.load(); },

    onYearChange(event) {
        const index = Number(event.detail.value);
        this.setData({ yearIndex: index, year: this.data.years[index] }, this.load);
    },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        api.request({ path: '/analytics/calendar/?year=' + this.data.year }).then(function (data) {
            const map = function (list) {
                return (list || []).map(function (x) {
                    return {
                        symbol: x.symbol,
                        assetName: x.asset_name,
                        accountName: x.account_name,
                        amount: f.money(x.amount),
                        currency: x.currency,
                        payDate: f.date(x.pay_date),
                        daysUntil: x.days_until,
                        origin: x.origin === 'transaction' ? '流水' : '明细',
                    };
                });
            };
            that.setData({
                loading: false,
                asOf: f.date(data.as_of),
                totals: data.totals,
                groups: [
                    { key: 'upcoming', title: '待派息', list: map(data.upcoming) },
                    { key: 'received', title: '已到账', list: map(data.received) },
                    { key: 'undated', title: '日期未定', list: map(data.undated) },
                ],
            });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },
});
