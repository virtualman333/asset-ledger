<template>
  <div v-loading="loading">
    <div class="page-head">
      <div><h2 class="page-title">统计</h2><div class="page-sub">收益、股息与成长分布 · 全部金额按基准币折算</div></div>
      <ActionButtonGroup>
        <el-select v-model="year" style="width: 110px" @change="load">
          <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="y" />
        </el-select>
        <el-button @click="exportCsv('records')">流水 CSV</el-button>
        <el-button @click="exportCsv('positions')">持仓 CSV</el-button>
        <el-button @click="exportCsv('dividends')">股息 CSV</el-button>
      </ActionButtonGroup>
    </div>

    <div class="stat-grid mb">
      <div v-for="m in metrics" :key="m.label" class="stat-card glass-card">
        <div class="stat-label">{{ m.label }}</div>
        <div class="stat-value tabular" :class="m.cls || ''">{{ m.value }}</div>
        <div class="stat-hint">{{ m.hint }}</div>
      </div>
    </div>

    <el-alert v-if="summary?.valuation_note" :title="summary.valuation_note" type="warning" :closable="false" show-icon class="mb" />

    <el-row :gutter="20" class="mb">
      <el-col :span="14">
        <el-card shadow="never">
          <template #header><span class="card-title">股息月度分布 · {{ year }}</span></template>
          <div ref="barRef" style="height: 300px"></div>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never">
          <template #header><span class="card-title">收益构成</span></template>
          <div ref="pieRef" style="height: 300px"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div class="row-between"><span class="card-title">股息日历</span><el-button link type="primary" @click="router.push('/calendar')">查看全部 →</el-button></div>
      </template>
      <div v-if="upcoming.length">
        <div v-for="u in upcoming.slice(0, 6)" :key="u.symbol + u.pay_date" class="cal-row">
          <span><strong>{{ u.symbol }}</strong> <span class="soft">{{ u.asset_name }}</span></span>
          <span class="soft">{{ u.pay_date }} · {{ u.days_until }} 天后</span>
          <strong class="tabular">{{ money(u.amount) }} {{ u.currency }}</strong>
        </div>
      </div>
      <div v-else class="empty-block">暂无待派息记录</div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';
import { api } from '@/api/client';
import type { Summary, CalendarData } from '@/api/types';
import { money, pct, trendClass, year as currentYear } from '@/api/format';
import ActionButtonGroup from '@/components/ActionButtonGroup.vue';

const router = useRouter();
const year = ref(currentYear());
const years = [currentYear() - 3, currentYear() - 2, currentYear() - 1, currentYear(), currentYear() + 1];
const loading = ref(true);
const summary = ref<Summary | null>(null);
const months = ref<{ month: string; net: string; count: number }[]>([]);
const upcoming = ref<CalendarData['upcoming']>([]);
const barRef = ref<HTMLElement>();
const pieRef = ref<HTMLElement>();
let barChart: echarts.ECharts | null = null;
let pieChart: echarts.ECharts | null = null;

const metrics = computed<{ label: string; value: string; cls?: string; hint?: string }[]>(() => {
  const s = summary.value;
  return [
    { label: '持仓成本', value: money(s?.cost_basis) },
    { label: '市值', value: money(s?.market_value) },
    { label: '浮动盈亏', value: money(s?.unrealized_pnl), cls: trendClass(s?.unrealized_pnl) },
    { label: '已实现盈亏', value: money(s?.realized_pnl), cls: trendClass(s?.realized_pnl) },
    { label: '累计股息', value: money(s?.dividend_total) },
    { label: '总盈亏', value: money(s?.total_pnl), cls: trendClass(s?.total_pnl) },
    { label: '近一年股息', value: money(s?.dividend_annual) },
    { label: '股息率', value: pct(s?.dividend_yield) },
    { label: '月度被动收入', value: money(s?.monthly_passive_income) },
    { label: 'XIRR 年化', value: pct(s?.annualized) },
  ];
});

function renderCharts() {
  const axisColor = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
  const lineColor = getComputedStyle(document.documentElement).getPropertyValue('--line').trim();

  if (barRef.value) {
    barChart ??= echarts.init(barRef.value);
    barChart.setOption({
      grid: { left: 50, right: 16, top: 24, bottom: 34 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: months.value.map((m) => m.month), axisLabel: { color: axisColor }, axisLine: { lineStyle: { color: lineColor } } },
      yAxis: { type: 'value', axisLabel: { color: axisColor }, splitLine: { lineStyle: { color: lineColor, type: 'dashed' } } },
      series: [{
        type: 'bar', data: months.value.map((m) => Number(m.net) || 0), barMaxWidth: 34,
        itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#9a8cff' }, { offset: 1, color: '#7166f0' },
        ]), borderRadius: [6, 6, 0, 0] },
      }],
    }, true);
  }

  if (pieRef.value && summary.value) {
    const s = summary.value;
    pieChart ??= echarts.init(pieRef.value);
    pieChart.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, textStyle: { color: axisColor } },
      series: [{
        type: 'pie', radius: ['48%', '72%'], center: ['50%', '44%'], avoidLabelOverlap: true,
        label: { show: false },
        data: [
          { value: Math.max(0, Number(s.market_value) || 0), name: '市值' },
          { value: Math.max(0, Number(s.unpriced_cost_basis) || 0), name: '未定价成本' },
        ],
        color: ['#7166f0', '#c9c5fa'],
      }],
    }, true);
  }
}

async function load() {
  loading.value = true;
  try {
    const [s, d, c] = await Promise.all([
      api.get<Summary>('/analytics/summary/'),
      api.get<{ months: { month: string; net: string; count: number }[] }>(`/analytics/dividends/?year=${year.value}`),
      api.get<CalendarData>(`/analytics/calendar/?year=${year.value}`),
    ]);
    summary.value = s;
    months.value = d.months;
    upcoming.value = c.upcoming;
    await nextTick();
    renderCharts();
  } finally {
    loading.value = false;
  }
}

async function exportCsv(kind: string) {
  const paths: Record<string, string> = {
    records: '/transactions/records/export/',
    positions: '/analytics/positions/export/',
    dividends: `/analytics/dividends/export/?year=${year.value}`,
  };
  try {
    await api.downloadCsv(paths[kind], `${kind}-${year.value}.csv`);
    ElMessage.success('已导出');
  } catch (e) { ElMessage.error((e as Error).message); }
}

onMounted(load);
window.addEventListener('resize', () => { barChart?.resize(); pieChart?.resize(); });
</script>

<style scoped>
.card-title { font-weight: 600; }
.row-between { display: flex; justify-content: space-between; align-items: center; }
.mb { margin-bottom: 20px; }
.cal-row { display: flex; justify-content: space-between; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.cal-row:last-child { border-bottom: none; }
@media (max-width: 980px) { .stat-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
