<template>
  <div v-loading="loading">
    <div class="hero glass-card">
      <div>
        <div class="hero-label">总资产 · {{ summary?.base_currency || 'CNY' }}</div>
        <div class="hero-value tabular">{{ totalAssets }}</div>
        <div class="hero-sub">
          市值 {{ money(summary?.market_value) }} · 未定价成本按成本计入
        </div>
      </div>
      <ActionButtonGroup>
        <el-button :loading="refreshing" @click="refreshQuotes"><el-icon><Refresh /></el-icon>刷新行情</el-button>
        <el-button type="primary" @click="router.push('/record')"><el-icon><Plus /></el-icon>记一笔</el-button>
      </ActionButtonGroup>
    </div>

    <el-alert v-if="summary?.valuation_note" :title="summary.valuation_note" type="warning" :closable="false" show-icon class="mb" />
    <el-alert v-if="summary?.fx_warning" title="部分币种使用兜底汇率，折算仅供参考" type="warning" :closable="false" show-icon class="mb" />

    <div class="stat-grid mb">
      <div class="stat-card glass-card">
        <div class="stat-label">浮动盈亏</div>
        <div class="stat-value tabular" :class="trendClass(summary?.unrealized_pnl)">{{ money(summary?.unrealized_pnl) }}</div>
      </div>
      <div class="stat-card glass-card">
        <div class="stat-label">已实现盈亏</div>
        <div class="stat-value tabular" :class="trendClass(summary?.realized_pnl)">{{ money(summary?.realized_pnl) }}</div>
      </div>
      <div class="stat-card glass-card">
        <div class="stat-label">累计股息</div>
        <div class="stat-value tabular">{{ money(summary?.dividend_total) }}</div>
        <div class="stat-hint">近一年 {{ money(summary?.dividend_annual) }}</div>
      </div>
      <div class="stat-card glass-card">
        <div class="stat-label">XIRR 年化</div>
        <div class="stat-value tabular">{{ pct(summary?.annualized) }}</div>
      </div>
    </div>

    <div class="page-head">
      <div>
        <h2 class="page-title">持仓明细</h2>
        <div class="page-sub">按账户与标的聚合，成本采用移动加权</div>
      </div>
      <el-tag effect="plain" size="small">{{ positions.length }} 个持仓</el-tag>
    </div>

    <el-card v-if="positions.length" shadow="never">
      <el-table :data="positions" row-key="key" @row-click="openTx" style="width: 100%" highlight-current-row>
        <el-table-column prop="symbol" label="标的" min-width="130">
          <template #default="{ row }">
            <strong>{{ row.symbol }}</strong>
            <div class="soft" style="font-size: 12px">{{ row.name }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="account_name" label="账户" min-width="110" />
        <el-table-column label="市场" width="90"><template #default="{ row }"><el-tag size="small" effect="plain">{{ marketLabel(row.market) }}</el-tag></template></el-table-column>
        <el-table-column label="数量 / 均价" min-width="150" align="right">
          <template #default="{ row }"><span class="tabular">{{ num(row.quantity, 2) }}</span><div class="soft tabular" style="font-size: 12px">@ {{ money(row.avg_cost, 4) }}</div></template>
        </el-table-column>
        <el-table-column label="现价" min-width="110" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.last_price, 4) }}</span></template></el-table-column>
        <el-table-column label="市值" min-width="130" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.market_value) }}</span></template></el-table-column>
        <el-table-column label="浮动盈亏" min-width="130" align="right"><template #default="{ row }"><span class="tabular" :class="trendClass(row.unrealized_pnl)">{{ money(row.unrealized_pnl) }}</span></template></el-table-column>
        <el-table-column label="近一年股息" min-width="120" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.annual_dividend) }}</span><div class="soft" style="font-size: 12px">{{ pct(row.dividend_yield) }}</div></template></el-table-column>
      </el-table>
    </el-card>
    <div v-else class="empty-block glass-card">还没有持仓，去「记一笔」开始记录吧。</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '@/api/client';
import type { Position, Summary } from '@/api/types';
import { money, num, pct, trendClass, marketLabel } from '@/api/format';
import ActionButtonGroup from '@/components/ActionButtonGroup.vue';

const router = useRouter();
const loading = ref(true);
const refreshing = ref(false);
const summary = ref<Summary | null>(null);
const positions = ref<(Position & { key: string })[]>([]);
const totalAssets = computed(() =>
  money((Number(summary.value?.market_value) || 0) + (Number(summary.value?.unpriced_cost_basis) || 0)),
);

async function load() {
  loading.value = true;
  try {
    const [s, p] = await Promise.all([
      api.get<Summary>('/analytics/summary/'),
      api.get<{ results: Position[] }>('/analytics/positions/'),
    ]);
    summary.value = s;
    positions.value = p.results.map((row) => ({ ...row, key: `${row.account_id}-${row.asset_id}` }));
  } finally {
    loading.value = false;
  }
}

async function refreshQuotes() {
  if (!positions.value.length) return;
  refreshing.value = true;
  try {
    const ids = positions.value.map((p) => p.asset_id).join(',');
    await api.get(`/market/quotes/?asset_ids=${ids}&refresh=1`);
    await load();
  } finally {
    refreshing.value = false;
  }
}

function openTx(row: Position) {
  router.push({ path: '/transactions', query: { asset: String(row.asset_id) } });
}

onMounted(load);
</script>

<style scoped>
.hero { padding: 28px 30px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; gap: 20px; flex-wrap: wrap; background: linear-gradient(120deg, var(--surface), var(--brand-soft)); }
.hero-label { font-size: 12px; color: var(--muted); letter-spacing: .1em; text-transform: uppercase; }
.hero-value { font-size: 40px; font-weight: 700; letter-spacing: -.02em; margin: 8px 0; }
.hero-sub { font-size: 13px; color: var(--text-soft); }
.mb { margin-bottom: 18px; }
</style>
