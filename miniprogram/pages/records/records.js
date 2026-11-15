const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        assetId: null,
        sideFilter: '',
        from: '',
        to: '',
        keyword: '',
        sides: ['全部', 'BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW'],
        sideLabels: ['全部', '买入', '卖出', '股息', '入金', '出金'],
        sideIndex: 0,
        records: [],
        loading: true,
        error: '',
        next: null,
        assetNames: {},
        accountNames: {},
    },

    onLoad(options) {
        if (!api.access()) { wx.reLaunch({ url: '/pages/index/index' }); return; }
        if (options.asset) this.setData({ assetId: Number(options.asset) });
        this.loadNames();
    },

    onShow() { if (api.access()) this.load(); },

    // 流水接口只回 asset/account 的 id，不回名字；这里单独取一次名字表。
    loadNames() {
        const that = this;
        Promise.all([
            api.request({ path: '/assets/?ordering=symbol' }),
            api.request({ path: '/accounts/' }),
        ]).then(function (values) {
            const assetNames = {};
            const accountNames = {};
            api.pageResults(values[0]).forEach(function (a) { assetNames[a.id] = a.symbol; });
            api.pageResults(values[1]).forEach(function (a) { accountNames[a.id] = a.name; });
            that.setData({ assetNames: assetNames, accountNames: accountNames }, that.load);
        }).catch(function () { /* 名字取不到时用 #id 兜底 */ });
    },

    displayRow(item) {
        const amount = Number(item.amount) || 0;
        return {
            id: item.id,
            sideLabel: f.sideLabel(item.side),
            symbol: this.data.assetNames[item.asset] || ('标的 #' + item.asset || '—'),
            accountName: this.data.accountNames[item.account] || ('账户 #' + item.account),
            amount: f.money(amount),
            trend: f.trend(amount),
            quantity: f.num(item.quantity, 4),
            price: f.money(item.price, 4),
            currency: item.currency,
            tradedAt: f.datetime(item.traded_at),
            note: item.note || '',
            source: item.source,
        };
    },

    onSideChange(event) {
        const index = Number(event.detail.value);
        this.setData({ sideIndex: index, sideFilter: index === 0 ? '' : this.data.sides[index] }, this.load);
    },
    onFieldInput(event) { this.setData({ [event.currentTarget.dataset.field]: event.detail.value }); },
    onFromChange(event) { this.setData({ from: event.detail.value }); },
    onToChange(event) { this.setData({ to: event.detail.value }); },
    applyFilters() { this.load(); },

    buildPath() {
        let path = '/transactions/records/?';
        const parts = [];
        if (this.data.sideFilter) parts.push('side=' + this.data.sideFilter);
        if (this.data.from) parts.push('from=' + this.data.from);
        if (this.data.to) parts.push('to=' + this.data.to);
        if (this.data.keyword) parts.push('search=' + encodeURIComponent(this.data.keyword));
        if (this.data.assetId) parts.push('asset=' + this.data.assetId);
        parts.push('ordering=-traded_at');
        return path + parts.join('&');
    },

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        return api.request({ path: this.buildPath() }).then(function (data) {
            const rows = api.pageResults(data).map(function (item) { return that.displayRow(item); });
            that.setData({ records: rows, next: data.next || null, loading: false });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },

    loadMore() {
        const that = this;
        if (!this.data.next) return;
        api.request({ path: this.data.next }).then(function (data) {
            const extra = api.pageResults(data).map(function (item) { return that.displayRow(item); });
            that.setData({ records: that.data.records.concat(extra), next: data.next || null });
        }).catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
    },

    remove(event) {
        const that = this;
        const id = event.currentTarget.dataset.id;
        wx.showModal({
            title: '删除流水',
            content: '删除后成本、持仓与收益会重新推导。确定删除？',
            success(res) {
                if (!res.confirm) return;
                api.request({ path: '/transactions/records/' + id + '/', method: 'DELETE' })
                    .then(function () { wx.showToast({ title: '已删除' }); that.load(); })
                    .catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
            },
        });
    },

    exportCsv() {
        api.downloadFile('/transactions/records/export/?ordering=-traded_at').then(function (filePath) {
            wx.shareFileMessage({ filePath: filePath, fail: function () { wx.showToast({ title: '文件已生成', icon: 'none' }); } });
        }).catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
    },
});
