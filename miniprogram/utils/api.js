// 与 Django REST backend (/api/v1) 的唯一网络入口。
// 约定：普通列表端点带分页信封，analytics/ingest 端点各自返回自己的形状。
const DEFAULT_BASE = 'https://fiance.virtualman.top/api/v1';
const KEY_BASE = 'al_api_base';
const KEY_ACCESS = 'al_access';
const KEY_REFRESH = 'al_refresh';

let refreshing = null;

function baseUrl() {
    const saved = wx.getStorageSync(KEY_BASE);
    return (saved || DEFAULT_BASE).replace(/\/+$/, '');
}

function setBaseUrl(url) {
    const clean = String(url || '').trim().replace(/\/+$/, '');
    wx.setStorageSync(KEY_BASE, clean || DEFAULT_BASE);
    getApp().globalData.apiBase = baseUrl();
}

function access() { return wx.getStorageSync(KEY_ACCESS) || ''; }
function refreshTokenValue() { return wx.getStorageSync(KEY_REFRESH) || ''; }

function setSession(accessToken, refreshToken) {
    wx.setStorageSync(KEY_ACCESS, accessToken || '');
    wx.setStorageSync(KEY_REFRESH, refreshToken || '');
}

function clearSession() {
    wx.removeStorageSync(KEY_ACCESS);
    wx.removeStorageSync(KEY_REFRESH);
}

function errorMessage(data, statusCode) {
    if (data && typeof data === 'object') {
        if (typeof data.detail === 'string') return data.detail;
        const keys = Object.keys(data);
        if (keys.length) {
            const first = data[keys[0]];
            if (Array.isArray(first)) return keys[0] + '：' + first.join('；');
            if (typeof first === 'string') return keys[0] + '：' + first;
        }
        try { return JSON.stringify(data); } catch (e) { return '请求失败'; }
    }
    return (data ? String(data) : '请求失败') + (statusCode ? '（HTTP ' + statusCode + '）' : '');
}

function doRaw(options) {
    return new Promise((resolve, reject) => {
        const url = /^https?:\/\//.test(options.path) ? options.path : baseUrl() + options.path;
        const header = Object.assign({ 'Content-Type': 'application/json' }, options.header || {});
        if (options.auth !== false && access()) header.Authorization = 'Bearer ' + access();
        wx.request({
            url: url,
            method: options.method || 'GET',
            data: options.data,
            header: header,
            // 普通请求让微信把 JSON 解析成对象；只有 CSV 下载才要求原文字符串。
            dataType: options.rawText ? 'text' : 'json',
            responseType: options.rawText ? 'text' : 'text',
            timeout: 30000,
            success(res) {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    resolve(res);
                    return;
                }
                const err = new Error(errorMessage(res.data, res.statusCode));
                err.status = res.statusCode;
                err.data = res.data;
                reject(err);
            },
            fail(err) { reject(new Error(err.errMsg || '网络请求失败，请检查后端地址与网络')); },
        });
    });
}

function request(options) {
    const opts = Object.assign({ auth: true, method: 'GET' }, options);
    return doRaw(opts).catch((err) => {
        if (err.status !== 401 || opts.auth === false || !refreshTokenValue() || opts._retried) {
            if (err.status === 401) {
                clearSession();
                wx.reLaunch({ url: '/pages/index/index' });
            }
            throw err;
        }
        if (!refreshing) {
            refreshing = doRaw({
                path: '/auth/token/refresh/',
                method: 'POST',
                auth: false,
                data: { refresh: refreshTokenValue() },
            }).then((res) => {
                setSession(res.data.access, res.data.refresh || refreshTokenValue());
            }).finally(() => { refreshing = null; });
        }
        return refreshing.then(() => {
            opts._retried = true;
            return request(opts);
        }).catch(() => {
            clearSession();
            wx.reLaunch({ url: '/pages/index/index' });
            throw err;
        });
    }).then((res) => res.data);
}

function login(username, password) {
    return request({ path: '/auth/token/', method: 'POST', auth: false, data: { username, password } })
        .then((data) => {
            setSession(data.access, data.refresh);
            return data;
        });
}

function register(username, password, email) {
    const payload = { username, password };
    if (email) payload.email = email;
    return request({ path: '/auth/register/', method: 'POST', auth: false, data: payload });
}

function uploadImage(filePath) {
    return new Promise((resolve, reject) => {
        const header = {};
        if (access()) header.Authorization = 'Bearer ' + access();
        wx.uploadFile({
            url: baseUrl() + '/ingest/image/',
            filePath: filePath,
            name: 'file',
            header: header,
            timeout: 60000,
            success(res) {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    let data = res.data;
                    if (typeof data === 'string') {
                        try { data = JSON.parse(data); } catch (e) { /* keep raw */ }
                    }
                    resolve(data);
                } else {
                    reject(new Error(errorMessage(res.data, res.statusCode)));
                }
            },
            fail(err) { reject(new Error(err.errMsg || '上传失败')); },
        });
    });
}

function downloadFile(path) {
    return doRaw({ path: path, method: 'GET', rawText: true })
        .then((res) => {
            const nameMatch = /filename\*=UTF-8''([^;]+)/i.exec(res.header['Content-Disposition'] || '');
            let fileName = 'export.csv';
            if (nameMatch) {
                try { fileName = decodeURIComponent(nameMatch[1]); } catch (e) { /* keep default */ }
            }
            const fs = wx.getFileSystemManager();
            const filePath = wx.env.USER_DATA_PATH + '/' + Date.now() + '-' + fileName;
            fs.writeFileSync(filePath, res.data, 'utf8');
            return filePath;
        });
}

function pageResults(data) {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    return data.results || [];
}

module.exports = {
    DEFAULT_BASE: DEFAULT_BASE,
    baseUrl: baseUrl,
    setBaseUrl: setBaseUrl,
    access: access,
    setSession: setSession,
    clearSession: clearSession,
    request: request,
    login: login,
    register: register,
    uploadImage: uploadImage,
    downloadFile: downloadFile,
    pageResults: pageResults,
    errorMessage: errorMessage,
};
