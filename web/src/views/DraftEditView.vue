<template>
  <div v-loading="loading" style="max-width: 900px">
    <div class="page-head">
      <div><h2 class="page-title">核对草稿</h2><div class="page-sub">AI 识别结果可改，改完才入账</div></div>
      <el-button @click="router.back()">返回</el-button>
    </div>

    <el-card shadow="never" class="mb">
      <div class="tag-row">
        <el-tag effect="plain">{{ draft?.kind === 'IMAGE' ? '截图' : '文本' }}</el-tag>
        <el-tag type="info" effect="plain">置信度 {{ confidence }}</el-tag>
        <el-tag v-for="m in missing" :key="m" type="warning" effect="plain">缺 {{ m }}</el-tag>
      </div>
      <div v-if="draft?.raw_text" class="raw soft">{{ draft.raw_text }}</div>
      <el-alert v-if="draft?.error" :title="draft.error" type="error" :closable="false" class="mt" />
    </el-card>

    <el-card shadow="never">
      <template #header><span class="card-title">可修改字段</span></template>
      <el-form label-position="top">
        <el-row :gutter="14">
          <el-col :span="12"><el-form-item label="标的代码"><el-input v-model="overrides.symbol" /></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="方向">
              <el-select v-model="overrides.side" style="width: 100%">
                <el-option v-for="(label, key) in SIDE_LABELS" :key="key" :label="label" :value="key" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="14">
          <el-col :span="12"><el-form-item label="数量"><el-input-number v-model="overrides.quantity" :precision="4" :min="0" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="单价"><el-input-number v-model="overrides.price" :precision="4" :min="0" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="14">
          <el-col :span="12"><el-form-item label="现金变动"><el-input-number v-model="overrides.amount" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12">
            <el-form-item label="账户">
              <el-select v-model="accountId" style="width: 100%" filterable>
                <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="币种"><el-input v-model="overrides.currency" /></el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="submitting" @click="confirm">确认入账</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { api, rows } from '@/api/client';
import type { Draft, Account } from '@/api/types';
import { SIDE_LABELS, pct } from '@/api/format';

const route = useRoute();
const router = useRouter();
const id = Number(route.params.id);
const loading = ref(true);
const submitting = ref(false);
const draft = ref<Draft | null>(null);
const accounts = ref<Account[]>([]);
const accountId = ref<number | null>(null);
const confidence = ref('—');
const missing = ref<string[]>([]);

const overrides = ref<Record<string, string | number>>({
  symbol: '', side: 'BUY', quantity: 0, price: 0, amount: 0, currency: 'CNY',
});

function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

async function load() {
  loading.value = true;
  try {
    const [job, acc] = await Promise.all([
      api.get<Draft>(`/ingest/jobs/${id}/`),
      api.get<{ results: Account[] }>('/accounts/'),
    ]);
    draft.value = job;
    confidence.value = pct(job.confidence, 0);
    missing.value = job.missing || [];
    accounts.value = rows(acc);
    accountId.value = accounts.value[0]?.id || null;

    const r = job.result || {};
    const side = String(r.side || 'BUY');
    overrides.value = {
      symbol: String(r.symbol || ''),
      side: SIDE_LABELS[side] ? side : 'BUY',
      quantity: toNumber(r.quantity) ?? 0,
      price: toNumber(r.price) ?? 0,
      amount: toNumber(r.amount) ?? 0,
      currency: String(r.currency || 'CNY'),
    };
  } finally {
    loading.value = false;
  }
}

async function confirm() {
  if (!overrides.value.symbol?.toString().trim()) return ElMessage.warning('请补充标的代码');
  submitting.value = true;
  try {
    await api.post(`/ingest/drafts/${id}/confirm/`, {
      overrides: overrides.value,
      ...(accountId.value ? { account_id: accountId.value } : {}),
    });
    ElMessage.success('已入账');
    router.push('/transactions');
  } catch (e) { ElMessage.error((e as Error).message); }
  finally { submitting.value = false; }
}

onMounted(load);
</script>

<style scoped>
.mb { margin-bottom: 18px; }
.mt { margin-top: 14px; }
.tag-row { display: flex; gap: 8px; flex-wrap: wrap; }
.raw { margin-top: 14px; padding: 12px; background: var(--surface-2); border-radius: 8px; font-size: 13px; line-height: 1.7; white-space: pre-wrap; }
.card-title { font-weight: 600; }
</style>
