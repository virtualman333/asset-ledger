const api = require('../../utils/api');
const f = require('../../utils/format');

Page({
    data: {
        drafts: [],
        text: '',
        loading: true,
        submitting: false,
        error: '',
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

    load() {
        const that = this;
        this.setData({ loading: true, error: '' });
        return api.request({ path: '/ingest/drafts/' }).then(function (data) {
            const rows = (data.results || []).map(function (item) {
                const result = item.result || {};
                const missing = item.missing || [];
                return {
                    id: item.id,
                    kind: item.kind === 'IMAGE' ? '截图' : '文本',
                    confidence: f.pct(item.confidence, 0),
                    sideLabel: f.sideLabel(result.side),
                    symbol: result.symbol || '未识别',
                    quantity: f.num(result.quantity, 4),
                    price: f.money(result.price, 4),
                    currency: result.currency || 'CNY',
                    missing: missing,
                    missingText: missing.length ? '待补：' + missing.join('、') : '信息完整，可确认',
                    error: item.error || '',
                    rawText: item.raw_text || '',
                    evidenceUrl: item.evidence_url || '',
                    createdAt: f.datetime(item.created_at),
                };
            });
            that.setData({ drafts: rows, loading: false });
        }).catch(function (err) {
            that.setData({ loading: false, error: err.message });
        });
    },

    onTextInput(event) { this.setData({ text: event.detail.value, error: '' }); },

    submitText() {
        const that = this;
        const text = (this.data.text || '').trim();
        if (!text) {
            this.setData({ error: '请输入成交短信或截图里的文字' });
            return;
        }
        this.setData({ submitting: true, error: '' });
        api.request({
            path: '/ingest/text/',
            method: 'POST',
            data: { text: text, client_request_id: 'mp-text-' + Date.now() },
        }).then(function (data) {
            that.setData({ text: '', submitting: false });
            if (data.status === 'SUCCEEDED') {
                wx.showToast({ title: '已生成草稿', icon: 'success' });
            } else {
                wx.showToast({ title: data.error || '未能识别，请手动记账', icon: 'none' });
            }
            that.load();
        }).catch(function (err) {
            that.setData({ submitting: false, error: err.message });
        });
    },

    chooseImage() {
        const that = this;
        wx.chooseMedia({
            count: 1,
            mediaType: ['image'],
            sourceType: ['album', 'camera'],
            success(res) {
                const file = res.tempFiles[0];
                that.setData({ submitting: true, error: '' });
                api.uploadImage(file.tempFilePath).then(function (data) {
                    that.setData({ submitting: false });
                    wx.showToast({ title: data.status === 'SUCCEEDED' ? '已识别，待确认' : '识别失败', icon: 'none' });
                    that.load();
                }).catch(function (err) {
                    that.setData({ submitting: false, error: err.message });
                });
            },
        });
    },

    openDraft(event) {
        const id = event.currentTarget.dataset.id;
        wx.navigateTo({ url: '/pages/draft-edit/draft-edit?id=' + id });
    },

    previewEvidence(event) {
        const url = event.currentTarget.dataset.url;
        if (!url) return;
        const api = require('../../utils/api');
        const full = /^https?:\/\//.test(url) ? url.replace(/^\/api\/v1/, api.baseUrl()) : api.baseUrl().replace(/\/api\/v1$/, '') + url;
        wx.previewImage({ urls: [full] });
    },

    discard(event) {
        const that = this;
        const id = event.currentTarget.dataset.id;
        wx.showModal({
            title: '丢弃草稿',
            content: '原始凭证会保留，仅草稿不再显示。确定丢弃？',
            success(res) {
                if (!res.confirm) return;
                api.request({ path: '/ingest/drafts/' + id + '/discard/', method: 'POST', data: {} })
                    .then(function () { wx.showToast({ title: '已丢弃' }); that.load(); })
                    .catch(function (err) { wx.showToast({ title: err.message, icon: 'none' }); });
            },
        });
    },
});
