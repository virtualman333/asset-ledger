<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">流水明细</h2><div class="page-sub">流水是唯一事实源，持仓与股息都由它推导</div></div>
      <ActionButtonGroup>
        <el-button @click="exportCsv"><el-icon><Download /></el-icon>导出 CSV</el-button>
        <el-button type="primary" @click="router.push('/record')"><el-icon><Plus /></el-icon>记一笔</el-button>
      </ActionButtonGroup>
    </div>

    <el-card shadow="never" class="mb">
      <div class="filters">
        <el-select v-model="filters.side" placeholder="全部方向" clearable style="width: 130px" @change="load(1)">
          <el-option v-for="(label, key) in SIDE_LABELS" :key="key" :label="label" :value="key" />
        </el-select>
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="起始" end-placeholder="结束" style="width: 250px" @change="load(1)" />
        <el-input v-model="filters.search" placeholder="搜索代码 / 备注" clearable style="width: 200px" @keyup.enter="load(1)" @clear="load(1)" />
        <el-button @click="load(1)">筛选</el-button>
      </div>
    </el-card>

    <el-card shadow="never">
      <el-table :data="records" v-loading="loading" style="width: 100%">
        <el-table-column label="时间" min-width="160"><template #default="{ row }"><span class="tabular">{{ datetime(row.traded_at) }}</span></template></el-table-column>
        <el-table-column label="方向" width="90"><template #default="{ row }"><el-tag size="small" :type="row.side === 'BUY' ? 'danger' : row.side === 'SELL' ? 'success' : 'info'" effect="plain">{{ sideLabel(row.side) }}</el-tag></template></el-table-column>
        <el-table-column label="标的" min-width="150"><template #default="{ row }"><strong>{{ assetNames[row.asset] || '—' }}</strong></template></el-table-column>
        <el-table-column label="账户" min-width="130"><template #default="{ row }">{{ accountNames[row.account] || '—' }}</template></el-table-column>
        <el-table-column label="数量" min-width="110" align="right"><template #default="{ row }"><span class="tabular">{{ num(row.quantity, 4) }}</span></template></el-table-column>
        <el-table-column label="单价" min-width="110" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.price, 4) }}</span></template></el-table-column>
        <el-table-column label="现金变动" min-width="140" align="right"><template #default="{ row }"><strong class="tabular" :class="trendClass(row.amount)">{{ money(row.amount) }}</strong><div class="soft" style="font-size: 11px">{{ row.currency }}</div></template></el-table-column>
        <el-table-column prop="note" label="备注" min-width="150" show-overflow-tooltip />
        <el-table-column label="操作" width="80" fixed="right"><template #default="{ row }"><el-button link type="danger" @click.stop="remove(row)">删除</el-button></template></el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination background layout="prev, pager, next" :total="total" :page-size="50" :current-page="page" @current-change="load" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { api, rows } from '@/api/client';
import type { Transaction, Account, Asset } from '@/api/types';
import { SIDE_LABELS, sideLabel, money, num, datetime, trendClass } from '@/api/format';
import ActionButtonGroup from '@/components/ActionButtonGroup.vue';

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const records = ref<Transaction[]>([]);
const total = ref(0);
const page = ref(1);
const range = ref<string[]>([]);
const assetNames = ref<Record<number, string>>({});
const accountNames = ref<Record<number, string>>({});
const filters = reactive({ side: '', search: '' });

function buildPath(): string {
  const parts: string[] = [];
  if (filters.side) parts.push(`side=${filters.side}`);
  if (range.value?.length === 2) parts.push(`from=${range.value[0]}`, `to=${range.value[1]}`);
  if (filters.search) parts.push(`search=${encodeURIComponent(filters.search)}`);
  if (route.query.asset) parts.push(`asset=${encodeURIComponent(String(route.query.asset))}`);
  parts.push('ordering=-traded_at', `page=${page.value}`);
  return `/transactions/records/?${parts.join('&')}`;
}

async function loadNames() {
  const [a, ac] = await Promise.all([
    api.get<{ results: Asset[] }>('/assets/?ordering=symbol'),
    api.get<{ results: Account[] }>('/accounts/'),
  ]);
  const am: Record<number, string> = {};
  const cm: Record<number, string> = {};
  rows(a).forEach((x) => { am[x.id] = x.symbol; });
  rows(ac).forEach((x) => { cm[x.id] = x.name; });
  assetNames.value = am;
  accountNames.value = cm;
}

async function load(p = page.value) {
  page.value = p;
  loading.value = true;
  try {
    const data = await api.get<{ count: number; results: Transaction[] }>(buildPath());
    records.value = data.results;
    total.value = data.count;
  } finally { loading.value = false; }
}

async function remove(t: Transaction) {
  try {
    await ElMessageBox.confirm('删除后成本、持仓与收益会重新推导。确定删除？', '删除流水', { type: 'warning' });
    await api.del(`/transactions/records/${t.id}/`);
    ElMessage.success('已删除');
    await load();
  } catch (e) { if ((e as Error).message !== 'cancel') ElMessage.error((e as Error).message); }
}

async function exportCsv() {
  try {
    await api.downloadCsv('/transactions/records/export/?ordering=-traded_at', 'transactions.csv');
    ElMessage.success('已导出');
  } catch (e) { ElMessage.error((e as Error).message); }
}

onMounted(async () => { await loadNames(); await load(1); });
</script>

<style scoped>
.mb { margin-bottom: 18px; }
.filters { display: flex; gap: 10px; flex-wrap: wrap; }
.pager { display: flex; justify-content: flex-end; margin-top: 18px; }
</style>
