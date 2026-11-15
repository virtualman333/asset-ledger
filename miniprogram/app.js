const api = require('./utils/api');

App({
    globalData: {
        appName: '股息账本',
        version: '1.0.0',
        apiBase: api.baseUrl(),
    },

    onLaunch() {
        // 会话只认本地保存的 access token；失效时由 api.request 自动刷新一次。
        this.globalData.apiBase = api.baseUrl();
    },
});
