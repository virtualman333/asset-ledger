<template>
  <div class="login-page">
    <div class="login-glow" />
    <div class="login-card glass-card">
      <div class="login-brand">
        <div class="login-mark">息</div>
        <h1>股息账本</h1>
        <p>股息视角的多市场资产记录 · Asset Ledger</p>
      </div>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="username" size="large" placeholder="用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" size="large" type="password" show-password placeholder="至少 8 位" autocomplete="current-password" @keyup.enter="submit" />
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="err" />
        <el-button type="primary" size="large" class="submit" :loading="loading" @click="submit">
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </el-button>
      </el-form>

      <div class="login-foot">
        <el-button link type="primary" @click="mode = mode === 'login' ? 'register' : 'login'">
          {{ mode === 'login' ? '没有账号？去注册' : '已有账号？去登录' }}
        </el-button>
        <el-button link @click="showSettings = !showSettings">后端地址</el-button>
      </div>

      <div v-if="showSettings" class="settings-inline">
        <el-input v-model="apiBaseInput" size="small" placeholder="https://fiance.virtualman.top/api/v1" />
        <el-button size="small" @click="saveBase">保存</el-button>
      </div>

      <div class="demo-tip">
        演示账号 <code>demo / demo12345</code> · 数据保存在你自己的服务器
      </div>
    </div>
    <div class="login-note">多市场 · 移动加权成本 · XIRR · 股息归集</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, apiBase, setApiBase } from '@/api/client';
import { auth } from '@/stores/auth';

const route = useRoute();
const router = useRouter();
const username = ref('demo');
const password = ref('demo12345');
const mode = ref<'login' | 'register'>('login');
const loading = ref(false);
const error = ref('');
const showSettings = ref(false);
const apiBaseInput = ref(apiBase());

async function submit() {
  if (!username.value.trim() || !password.value) {
    error.value = '请填写账号和密码';
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    if (mode.value === 'register') {
      await api.register(username.value.trim(), password.value);
    }
    await api.login(username.value.trim(), password.value);
    auth.syncToken();
    const me = await api.me<{ username: string }>().catch(() => null);
    auth.setUser(me?.username || username.value.trim());
    router.push((route.query.redirect as string) || '/holdings');
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

function saveBase() {
  setApiBase(apiBaseInput.value);
  auth.setBase(apiBaseInput.value);
  showSettings.value = false;
}
</script>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; position: relative; padding: 24px; }
.login-glow {
  position: fixed; width: 720px; height: 720px; border-radius: 50%;
  background: radial-gradient(circle, rgba(113,102,240,.28), transparent 65%);
  top: -280px; left: 50%; transform: translateX(-50%); pointer-events: none;
}
.login-card { width: 100%; max-width: 430px; padding: 38px 38px 28px; position: relative; }
.login-brand { text-align: center; margin-bottom: 26px; }
.login-mark {
  width: 56px; height: 56px; border-radius: 16px; margin: 0 auto 16px;
  display: grid; place-items: center; color: #fff; font-size: 26px; font-weight: 700;
  background: linear-gradient(135deg, #7166f0, #9a8cff);
  box-shadow: 0 12px 30px rgba(113,102,240,.36);
}
.login-brand h1 { margin: 0; font-size: 26px; letter-spacing: -.02em; }
.login-brand p { margin: 8px 0 0; color: var(--muted); font-size: 13px; }
.submit { width: 100%; height: 44px; }
.err { margin-bottom: 14px; }
.login-foot { display: flex; justify-content: space-between; margin-top: 14px; }
.settings-inline { display: flex; gap: 8px; margin-top: 12px; }
.demo-tip {
  margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--line);
  text-align: center; font-size: 12px; color: var(--muted);
}
.demo-tip code { background: var(--surface-2); padding: 2px 6px; border-radius: 5px; }
.login-note { position: fixed; bottom: 24px; font-size: 12px; color: var(--muted); letter-spacing: .08em; }
</style>
