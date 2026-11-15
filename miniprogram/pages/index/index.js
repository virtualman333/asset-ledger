const api = require('../../utils/api');

Page({
    data: {
        appName: '股息账本',
        tagline: '股息视角的多市场资产记录',
        username: '',
        password: '',
        email: '',
        apiBase: api.baseUrl(),
        mode: 'login',
        loading: false,
        error: '',
        showSettings: false,
    },

    onLoad() {
        if (api.access()) {
            wx.switchTab({ url: '/pages/holdings/holdings' });
        }
        this.setData({ apiBase: api.baseUrl() });
    },

    onFieldInput(event) {
        const field = event.currentTarget.dataset.field;
        this.setData({ [field]: event.detail.value, error: '' });
    },

    toggleMode() {
        this.setData({ mode: this.data.mode === 'login' ? 'register' : 'login', error: '' });
    },

    toggleSettings() {
        this.setData({ showSettings: !this.data.showSettings });
    },

    saveBase() {
        api.setBaseUrl(this.data.apiBase);
        this.setData({ apiBase: api.baseUrl(), showSettings: false });
        wx.showToast({ title: '后端地址已保存', icon: 'success' });
    },

    submit() {
        const that = this;
        const username = (this.data.username || '').trim();
        const password = this.data.password || '';
        if (!username || !password) {
            this.setData({ error: '请填写账号和密码' });
            return;
        }
        this.setData({ loading: true, error: '' });
        const task = this.data.mode === 'login'
            ? api.login(username, password)
            : api.register(username, password, this.data.email.trim()).then(function () {
                return api.login(username, password);
            });
        task.then(function () {
            that.setData({ loading: false });
            wx.showToast({ title: '欢迎回来', icon: 'success' });
            setTimeout(function () { wx.switchTab({ url: '/pages/holdings/holdings' }); }, 400);
        }).catch(function (err) {
            that.setData({ loading: false, error: err.message });
        });
    },
});
