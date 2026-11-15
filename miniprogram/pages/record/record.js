const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        sides: f.SIDES,
        sideLabels: f.SIDE_LABELS,
        sideIndex: 0,
        side: 'BUY',
        markets: f.MARKETS,
        marketLabels: f.MARKET_LABELS,
        marketIndex: 0,
        currencies: f.CURRENCIES,
        currencyIndex: 0,
        accounts: [],
        accountNames: [],
        accountIndex: 0,
        assetId: null,
        symbol: '',
        assetName: '',
        quantity: '',
        price: '',
        amount: '',
        fee: '0',
        tax: '0',
        perShare: '',
        shares: '',
        net: '',
        note: '',
        tradedDate: f.today(),
        tradedTime: '09:30',
        preview: '',
        loading: false,
        error: '',
        needAsset: true,
        needAmount: false,
        needDividend: false,
    },

    onLoad() {
        if (!api.access()) {
            wx.reLaunch({ url: '/pages/index/index' });
            return;
        }
        this.loadAccounts();
    },

    onShow() {
        if (api.access() && this.data.accounts.length === 0) this.loadAccounts();
    },

    loadAccounts() {
        const that = this;
        api.request({ path: '/accounts/?page_size=200' }).then(function (data) {
            const accounts = api.pageResults(data);
            that.setData({
                accounts: accounts,
                accountNames: accounts.map(function (a) { return a.name + '（' + a.currency + '）'; }),
                accountIndex: 0,
            });
        }).catch(function (err) {
            that.setData({ error: err.message });
        });
    },

    onFieldInput(event) {
        const field = event.currentTarget.dataset.field;
        const patch = { [field]: event.detail.value, error: '' };
        if (field === 'symbol') patch.assetId = null;
        this.setData(patch);
        this.updatePreview();
    },

    onSideIndexTap(event) {
        const index = Number(event.currentTarget.dataset.index);
        this.applySide(index);
    },

    onSideChange(event) {
        this.applySide(Number(event.detail.value));
    },

    applySide(index) {
        const side = this.data.sides[index];
        this.setData({
            sideIndex: index,
            side: side,
            needAsset: side === 'BUY' || side === 'SELL' || side === 'DIVIDEND',
            needAmount: side === 'DEPOSIT' || side === 'WITHDRAW',
            needDividend: side === 'DIVIDEND',
            error: '',
        });
        this.updatePreview();
    },

    onMarketChange(event) {
        const index = Number(event.detail.value);
        this.setData({
            marketIndex: index,
            currencyIndex: Math.max(0, this.data.currencies.indexOf(f.currencyForMarket(this.data.markets[index]))),
        });
    },

    onCurrencyChange(event) {
        this.setData({ currencyIndex: Number(event.detail.value) });
    },

    onAccountChange(event) {
        this.setData({ accountIndex: Number(event.detail.value) });
    },

    onDateChange(event) { this.setData({ tradedDate: event.detail.value }); },
    onTimeChange(event) { this.setData({ tradedTime: event.detail.value }); },

    updatePreview() {
        const d = this.data;
        let text = '';
        if (d.needAmount) {
            text = f.sideLabel(d.side) + ' ' + f.money(d.amount) + ' ' + d.currencies[d.currencyIndex];
        } else if (d.needDividend) {
            const gross = (Number(d.perShare) || 0) * (Number(d.shares) || 0);
            const net = d.net === '' ? gross - (Number(d.tax) || 0) : Number(d.net);
            text = '股息 ' + f.sideLabel(d.side) + ' · 税前 ' + f.money(gross) + ' / 到手 ' + f.money(net);
        } else {
            const amount = (Number(d.quantity) || 0) * (Number(d.price) || 0);
            const cash = d.side === 'BUY' ? -(amount + (Number(d.fee) || 0) + (Number(d.tax) || 0)) : amount - (Number(d.fee) || 0) - (Number(d.tax) || 0);
            text = f.sideLabel(d.side) + ' ' + d.symbol + ' · 现金变动 ' + f.money(cash) + ' ' + d.currencies[d.currencyIndex];
        }
        this.setData({ preview: text });
    },

    findAsset(symbol, market, currency) {
        const that = this;
        return api.request({ path: '/assets/?search=' + encodeURIComponent(symbol) }).then(function (data) {
            const list = api.pageResults(data);
            const exact = list.filter(function (a) { return a.symbol === symbol && a.market === market; })[0];
            if (exact) return exact;
            return api.request({
                path: '/assets/',
                method: 'POST',
                data: {
                    symbol: symbol,
                    name: that.data.assetName || symbol,
                    market: market,
                    currency: currency,
                    is_dividend_asset: that.data.side === 'DIVIDEND',
                },
            });
        });
    },

    submit() {
        const that = this;
        const d = this.data;
        this.setData({ error: '', loading: true });
        if (!d.accounts.length) {
            this.setData({ loading: false, error: '请先到「我的 → 账户管理」创建账户' });
            return;
        }
        if (d.needAsset && !d.symbol.trim()) {
            this.setData({ loading: false, error: '请填写标的代码' });
            return;
        }
        if (d.needAmount && !d.amount) {
            this.setData({ loading: false, error: '入金 / 出金必须填写金额' });
            return;
        }
        const accountId = d.accounts[d.accountIndex].id;
        const market = d.markets[d.marketIndex];
        const currency = d.currencies[d.currencyIndex];
        const tradedAt = d.tradedDate + 'T' + d.tradedTime + ':00';
        const task = d.needAsset
            ? this.findAsset(d.symbol.trim(), market, currency)
            : Promise.resolve(null);
        task.then(function (asset) {
            let path;
            let payload;
            if (d.side === 'DIVIDEND') {
                const gross = (Number(d.perShare) || 0) * (Number(d.shares) || 0);
                const net = d.net === '' ? gross - (Number(d.tax) || 0) : Number(d.net);
                path = '/transactions/dividends/';
                payload = {
                    asset: asset.id,
                    account: accountId,
                    pay_date: d.tradedDate,
                    amount_per_share: d.perShare || null,
                    shares: d.shares || null,
                    gross: gross,
                    tax: Number(d.tax) || 0,
                    net: net,
                    currency: currency,
                };
            } else {
                path = '/transactions/records/';
                payload = {
                    account: accountId,
                    asset: asset ? asset.id : null,
                    side: d.side,
                    currency: currency,
                    traded_at: tradedAt,
                    fee: Number(d.fee) || 0,
                    tax: Number(d.tax) || 0,
                    note: d.note,
                    client_request_id: 'mp-' + Date.now() + '-' + Math.floor(Math.random() * 10000),
                };
                if (d.needAmount) payload.amount = Math.abs(Number(d.amount));
                else {
                    payload.quantity = Number(d.quantity);
                    payload.price = Number(d.price);
                }
            }
            return api.request({ path: path, method: 'POST', data: payload });
        }).then(function () {
            that.setData({
                loading: false,
                symbol: '', assetName: '', assetId: null,
                quantity: '', price: '', amount: '', perShare: '', shares: '', net: '', note: '',
                preview: '',
            });
            wx.showToast({ title: '已记录', icon: 'success' });
        }).catch(function (err) {
            that.setData({ loading: false, error: err.message });
        });
    },
});
