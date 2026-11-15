const api = require('../../utils/api');

Page({
    data: {
        csv: '',
        dryRun: true,
        prefix: 'mp-import-' + Date.now(),
        result: null,
        loading: false,
        error: '',
    },

    onLoad() { if (!api.access()) wx.reLaunch({ url: '/pages/index/index' }); },

    chooseFile() {
        const that = this;
        wx.chooseMessageFile({
            count: 1,
            type: 'file',
            extension: ['csv'],
            success(res) {
                const file = res.tempFiles[0];
                const fs = wx.getFileSystemManager();
                fs.readFile({
                    filePath: file.path,
                    encoding: 'utf8',
                    success(read) { that.setData({ csv: read.data, error: '' }); },
                    fail() { that.setData({ error: '文件读取失败，请确认是 UTF-8 编码的 CSV' }); },
                });
            },
        });
    },

    onCsvInput(event) { this.setData({ csv: event.detail.value }); },
    onDryRunChange(event) { this.setData({ dryRun: event.detail.value }); },
    onPrefixInput(event) { this.setData({ prefix: event.detail.value }); },

    submit() {
        const that = this;
        if (!this.data.csv.trim()) { this.setData({ error: '请先选择或粘贴 CSV 内容' }); return; }
        this.setData({ loading: true, error: '', result: null });
        api.request({
            path: '/transactions/records/import/',
            method: 'POST',
            data: {
                csv: this.data.csv,
                dry_run: this.data.dryRun,
                client_request_id_prefix: this.data.prefix || ('mp-import-' + Date.now()),
            },
        }).then(function (data) {
            that.setData({
                loading: false,
                result: {
                    headline: (data.dry_run ? '校验结果' : '导入结果') + '：共 ' + data.total + ' 行，成功 ' + data.created + '，跳过 ' + data.skipped + '，失败 ' + data.failed.length,
                    failed: data.failed,
                },
            });
        }).catch(function (err) { that.setData({ loading: false, error: err.message }); });
    },
});
