<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">行情与汇率</h2><div class="page-sub">免费源约 15 分钟一轮；拿不到价时标记旧价而非写 0</div></div>
      <el-button :loading="refreshing" @click="load(true)"><el-icon><Refresh /></el-icon>强制刷新</el-button>
    </div>

    <el-card shadow="never" class="mb">
      <el-table :data="quotes" v-loading="loading" style="width: 100%">
        <el-table-column label="标的" min-width="150"><template #default="{ row }"><strong>{{ row.symbol }}</strong><div class="soft" style="font-size: 12px">{{ row.name }}</div></template></el-table-column>
        <el-table-column label="现价" min-width="130" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.price, 4) }}</span></template></el-table-column>
        <el-table-column label="涨跌幅" min-width="110" align="right"><template #default="{ row }"><span class="tabular" :class="trendClass(row.change_pct)">{{ row.change_pct === undefined ? '—' : pct(row.change_pct) }}</span></template></el-table-column>
        <el-table-column prop="currency" label="币种" width="90" />
        <el-table-column label="来源" min-width="100"><template #default="{ row }"><el-tag size="small" effect="plain">{{ row.source || '—' }}</el-tag></template></el-table-column>
        <el-table-column label="抓取时间" min-width="170"><template #default="{ row }"><span class="soft tabular" style="font-size: 12px">{{ datetime(row.fetched_at) }}</span></template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag v-if="row.stale" type="warning" size="small" effect="plain">旧价</el-tag><el-tag v-else type="success" size="small" effect="plain">最新</el-tag></template></el-table-column>
      </el-table>
      <div v-if="!quotes.length && !loading" class="empty-block">还没有持仓标的。</div>
    </el-card>

    <el-card shadow="never" style="max-width: 640px">
      <template #header><span class="card-title">汇率查询</span></template>
      <div class="fx-row">
        <el-select v-model="base" style="width: 120px"><el-option v-for="c in CURRENCIES" :key="c" :label="c" :value="c" /></el-select>
        <span class="soft">→</span>
        <el-select v-model="quote" style="width: 120px"><el-option v-for="c in CURRENCIES" :key="c" :label="c" :value="c" /></el-select>
        <el-button type="primary" :loading="fxLoading" @click="loadFx">查询</el-button>
      </div>
      <el-alert v-if="fxResult" :title="fxResult" type="info" :closable="false" class="mt" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { api } from '@/api/client';
import type { Quote, Position } from '@/api/types';
import { CURRENCIES, money, pct, datetime, trendClass, num } from '@/api/format';

const loading = ref(true);
const refreshing = ref(false);
const fxLoading = ref(false);
const quotes = ref<Quote[]>([]);
const base = ref('USD');
const quote = ref('CNY');
const fxResult = ref('');

async function load(force = false) {
  loading.value = true;
  refreshing.value = force;
  try {
    const positions = await api.get<{ results: Position[] }>('/analytics/positions/');
    const ids = positions.results.map((p) => p.asset_id);
    if (!ids.length) { quotes.value = []; return; }
    const data = await api.get<{ results: Quote[] }>(`/market/quotes/?asset_ids=${ids.join(',')}${force ? '&refresh=1' : ''}`);
    quotes.value = data.results;
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

async function loadFx() {
  fxLoading.value = true;
  try {
    const d = await api.get<{ base: string; quote: string; rate: string; source?: string; stale?: boolean }>(`/market/fx/?base=${base.value}&quote=${quote.value}`);
    fxResult.value = `1 ${d.base} = ${num(d.rate, 6)} ${d.quote} · ${d.source || '—'}${d.stale ? ' · 旧汇率' : ''}`;
  } catch (e) { fxResult.value = (e as Error).message; }
  finally { fxLoading.value = false; }
}

onMounted(load);
</script>

<style scoped>
.mb { margin-bottom: 18px; }
.mt { margin-top: 16px; }
.fx-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.card-title { font-weight: 600; }
</style>
