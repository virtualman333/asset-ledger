<template>
  <div v-loading="loading">
    <div class="page-head">
      <div><h2 class="page-title">股息日历</h2><div class="page-sub">由已录入的股息推导，不预测未来公告</div></div>
      <el-select v-model="year" style="width: 120px" @change="load">
        <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="y" />
      </el-select>
    </div>

    <div class="stat-grid mb">
      <div class="stat-card glass-card"><div class="stat-label">股息笔数</div><div class="stat-value tabular">{{ data?.totals?.count ?? 0 }}</div></div>
      <div class="stat-card glass-card"><div class="stat-label">下一派息日</div><div class="stat-value tabular" style="font-size: 20px">{{ date(data?.totals?.next_pay_date) }}</div></div>
      <div class="stat-card glass-card"><div class="stat-label">基准日</div><div class="stat-value tabular" style="font-size: 20px">{{ date(data?.as_of) }}</div></div>
    </div>

    <el-row :gutter="18">
      <el-col v-for="g in groups" :key="g.key" :span="8" class="mb">
        <el-card shadow="never" class="group-card">
          <template #header><div class="row-between"><strong>{{ g.title }}</strong><el-tag size="small" effect="plain">{{ g.list.length }}</el-tag></div></template>
          <div v-if="g.list.length">
            <div v-for="(item, i) in g.list" :key="i" class="item">
              <div class="row-between"><strong>{{ item.symbol }}</strong><span class="tabular">{{ money(item.amount) }} {{ item.currency }}</span></div>
              <div class="soft" style="font-size: 12px; margin-top: 4px">{{ item.account_name }} · {{ item.origin === 'transaction' ? '流水录入' : '股息明细' }}</div>
              <div class="soft" style="font-size: 12px; margin-top: 2px">{{ date(item.pay_date) }}<span v-if="item.days_until !== null"> · {{ item.days_until }} 天</span></div>
            </div>
          </div>
          <div v-else class="empty-block" style="padding: 28px 0">暂无</div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { api } from '@/api/client';
import type { CalendarData, CalendarEntry } from '@/api/types';
import { money, date, year as currentYear } from '@/api/format';

const year = ref(currentYear());
const years = [currentYear() - 2, currentYear() - 1, currentYear(), currentYear() + 1];
const loading = ref(true);
const data = ref<CalendarData | null>(null);
const groups = computed(() => [
  { key: 'upcoming', title: '待派息', list: data.value?.upcoming || [] as CalendarEntry[] },
  { key: 'received', title: '已到账', list: data.value?.received || [] as CalendarEntry[] },
  { key: 'undated', title: '日期未定', list: data.value?.undated || [] as CalendarEntry[] },
]);

async function load() {
  loading.value = true;
  try { data.value = await api.get<CalendarData>(`/analytics/calendar/?year=${year.value}`); }
  finally { loading.value = false; }
}
onMounted(load);
</script>

<style scoped>
.mb { margin-bottom: 18px; }
.row-between { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.item { padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.item:last-child { border-bottom: none; }
.group-card { min-height: 260px; }
@media (max-width: 1100px) { .group-card { min-height: auto; } }
</style>
