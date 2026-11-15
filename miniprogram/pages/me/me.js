const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        user: null,
        username: '',
        apiBase: api.baseUrl(),
        version: '1.0.0',
        showBase: false,
        menuGroups: [
            {
                title: '账本',
                items: [
                    { key: 'records', text: '流水明细', path: '/pages/records/records' },
                    { key: 'dividends', text: '股息明细', path: '/pages/dividends/dividends' },
                    { key: 'calendar', text: '股息日历', path: '/pages/calendar/calendar' },
                    { key: 'import', text: '导入流水 CSV', path: '/pages/import/import' },
                ],
            },
            {
                title: '配置',
                items: [
                    { key: 'accounts', text: '账户管理', path: '/pages/accounts/accounts' },
                    { key: 'assets', text: '标的库', path: '/pages/assets/assets' },
                    { key: 'quotes', text: '行情与汇率', path: '/pages/quotes/quotes' },
                ],
            },
        ],
    },

    onLoad() {
        if (!api.access()) {
            wx.reLaunch({ url: '/pages/index/index' });
            return;
        }
    },

    onShow() {
        if (api.access()) this.loadUser();
        this.setData({ apiBase: api.baseUrl() });
    },

    loadUser() {
        const that = this;
        api.request({ path: '/auth/me/' }).then(function (data) {
            that.setData({ user: data, username: data.username });
        }).catch(function () { /* token 失效由 api 层处理 */ });
    },

    toggleBase() { this.setData({ showBase: !this.data.showBase }); },

    onBaseInput(event) { this.setData({ apiBase: event.detail.value }); },

    saveBase() {
        api.setBaseUrl(this.data.apiBase);
        this.setData({ showBase: false, apiBase: api.baseUrl() });
        wx.showToast({ title: '已保存', icon: 'success' });
    },

    handleMenuTap(event) {
        const path = event.currentTarget.dataset.path;
        if (!path) return;
        wx.navigateTo({ url: path });
    },

    logout() {
        const that = this;
        wx.showModal({
            title: '退出登录',
            content: '本机保存的登录凭证会被清除，账本数据仍在服务器。',
            success(res) {
                if (!res.confirm) return;
                api.clearSession();
                that.setData({ user: null });
                wx.reLaunch({ url: '/pages/index/index' });
            },
        });
    },
});
