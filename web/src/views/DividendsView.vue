<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">股息明细</h2><div class="page-sub">金额为税后到手净额，与统计页「累计股息」同口径</div></div>
      <div class="toolbar">
        <el-select v-model="year" style="width: 110px" @change="load">
          <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="y" />
        </el-select>
        <el-button type="primary" @click="openAdd"><el-icon><Plus /></el-icon>记录股息</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table :data="records" v-loading="loading" style="width: 100%">
        <el-table-column label="标的" min-width="150"><template #default="{ row }"><strong>{{ assetNames[row.asset] || `标的 #${row.asset}` }}</strong></template></el-table-column>
        <el-table-column label="账户" min-width="130"><template #default="{ row }">{{ accountNames[row.account] || '—' }}</template></el-table-column>
        <el-table-column label="派息日" width="130"><template #default="{ row }"><span class="tabular">{{ date(row.pay_date) }}</span></template></el-table-column>
        <el-table-column label="税前" min-width="120" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.gross) }}</span></template></el-table-column>
        <el-table-column label="税费" min-width="110" align="right"><template #default="{ row }"><span class="tabular">{{ money(row.tax) }}</span></template></el-table-column>
        <el-table-column label="到手" min-width="120" align="right"><template #default="{ row }"><strong class="tabular">{{ money(row.net) }}</strong><div class="soft" style="font-size: 11px">{{ row.currency }}</div></template></el-table-column>
        <el-table-column label="再投资" width="90"><template #default="{ row }"><el-tag v-if="row.reinvested" type="success" size="small" effect="plain">是</el-tag><span v-else class="muted">否</span></template></el-table-column>
        <el-table-column label="操作" width="80" fixed="right"><template #default="{ row }"><el-button link type="danger" @click="remove(row)">删除</el-button></template></el-table-column>
      </el-table>
      <div v-if="!records.length && !loading" class="empty-block">这一年还没有股息明细。</div>
    </el-card>

    <el-dialog v-model="dialog" title="记录一笔股息" width="520px">
      <el-form label-position="top">
        <el-form-item label="标的"><el-select v-model="form.asset" filterable style="width: 100%"><el-option v-for="a in assets" :key="a.id" :label="`${a.symbol} ${a.name}`" :value="a.id" /></el-select></el-form-item>
        <el-form-item label="账户"><el-select v-model="form.account" clearable style="width: 100%"><el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" /></el-select></el-form-item>
        <el-form-item label="派息日"><el-date-picker v-model="form.pay_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="税前金额"><el-input-number v-model="form.gross" :precision="2" :min="0" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="税费"><el-input-number v-model="form.tax" :precision="2" :min="0" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="到手净额（留空按税前-税费）"><el-input-number v-model="form.net" :precision="2" :min="0" style="width: 100%" /></el-form-item>
        <el-form-item label="币种"><el-select v-model="form.currency" style="width: 100%"><el-option v-for="c in CURRENCIES" :key="c" :label="c" :value="c" /></el-select></el-form-item>
        <el-form-item label="再投资"><el-switch v-model="form.reinvested" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { api, rows } from '@/api/client';
import type { DividendRecord, Account, Asset } from '@/api/types';
import { CURRENCIES, money, date, year as currentYear } from '@/api/format';

const year = ref(currentYear());
const years = [currentYear() - 2, currentYear() - 1, currentYear(), currentYear() + 1];
const loading = ref(true);
const saving = ref(false);
const dialog = ref(false);
const records = ref<DividendRecord[]>([]);
const assets = ref<Asset[]>([]);
const accounts = ref<Account[]>([]);
const assetNames = ref<Record<number, string>>({});
const accountNames = ref<Record<number, string>>({});
const form = reactive({ asset: 0, account: null as number | null, pay_date: '', gross: 0, tax: 0, net: null as number | null, currency: 'CNY', reinvested: false });

async function loadOptions() {
  const [a, ac] = await Promise.all([
    api.get<{ results: Asset[] }>('/assets/?ordering=symbol'),
    api.get<{ results: Account[] }>('/accounts/'),
  ]);
  assets.value = rows(a);
  accounts.value = rows(ac);
  assets.value.forEach((x) => { assetNames.value[x.id] = x.symbol; });
  accounts.value.forEach((x) => { accountNames.value[x.id] = x.name; });
}

async function load() {
  loading.value = true;
  try { records.value = rows(await api.get<{ results: DividendRecord[] }>(`/transactions/dividends/?year=${year.value}`)); }
  finally { loading.value = false; }
}

function openAdd() {
  Object.assign(form, { asset: assets.value[0]?.id || 0, account: accounts.value[0]?.id || null, pay_date: new Date().toISOString().slice(0, 10), gross: 0, tax: 0, net: null, currency: 'CNY', reinvested: false });
  dialog.value = true;
}

async function save() {
  if (!form.asset) return ElMessage.warning('请先创建标的');
  saving.value = true;
  try {
    const gross = form.gross || 0;
    await api.post('/transactions/dividends/', {
      asset: form.asset, account: form.account, pay_date: form.pay_date,
      gross, tax: form.tax || 0,
      net: form.net === null ? gross - (form.tax || 0) : form.net,
      currency: form.currency, reinvested: form.reinvested,
    });
    ElMessage.success('已记录');
    dialog.value = false;
    await load();
  } catch (e) { ElMessage.error((e as Error).message); }
  finally { saving.value = false; }
}

async function remove(d: DividendRecord) {
  try {
    await ElMessageBox.confirm('删除这条股息记录？', '确认', { type: 'warning' });
    await api.del(`/transactions/dividends/${d.id}/`);
    ElMessage.success('已删除');
    await load();
  } catch (e) { if ((e as Error).message !== 'cancel') ElMessage.error((e as Error).message); }
}

onMounted(async () => { await loadOptions(); await load(); });
</script>
