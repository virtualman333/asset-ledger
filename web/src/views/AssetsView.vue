<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">标的库</h2><div class="page-sub">标的全局共享，按「市场+代码」唯一</div></div>
      <el-button type="primary" @click="openAdd"><el-icon><Plus /></el-icon>新增标的</el-button>
    </div>

    <el-card shadow="never" class="mb">
      <el-input v-model="search" placeholder="搜索代码或名称" clearable style="max-width: 320px" @keyup.enter="load" @clear="load">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button style="margin-left: 10px" @click="load">搜索</el-button>
    </el-card>

    <el-card shadow="never">
      <el-table :data="assets" v-loading="loading" style="width: 100%">
        <el-table-column prop="symbol" label="代码" min-width="120"><template #default="{ row }"><strong>{{ row.symbol }}</strong></template></el-table-column>
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="市场" width="100"><template #default="{ row }"><el-tag size="small" effect="plain">{{ marketLabel(row.market) }}</el-tag></template></el-table-column>
        <el-table-column label="类型" width="100"><template #default="{ row }">{{ ASSET_TYPE_LABELS[row.asset_type] || row.asset_type }}</template></el-table-column>
        <el-table-column prop="currency" label="币种" width="90" />
        <el-table-column label="股息标的" width="100"><template #default="{ row }"><el-tag v-if="row.is_dividend_asset" type="success" size="small" effect="plain">是</el-tag><span v-else class="muted">否</span></template></el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="openEdit(row)">编辑</el-button><el-button link type="danger" @click="remove(row)">删除</el-button></template>
        </el-table-column>
      </el-table>
      <div v-if="!assets.length && !loading" class="empty-block">没有匹配的标的。</div>
    </el-card>

    <el-dialog v-model="dialog" :title="form.id ? '编辑标的' : '新增标的'" width="540px">
      <el-form label-position="top">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="代码"><el-input v-model="form.symbol" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="名称"><el-input v-model="form.name" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="市场"><el-select v-model="form.market" style="width: 100%"><el-option v-for="(label, key) in MARKET_LABELS" :key="key" :label="label" :value="key" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="类型"><el-select v-model="form.asset_type" style="width: 100%"><el-option v-for="key in ASSET_TYPES" :key="key" :label="ASSET_TYPE_LABELS[key]" :value="key" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="币种"><el-select v-model="form.currency" style="width: 100%"><el-option v-for="c in CURRENCIES" :key="c" :label="c" :value="c" /></el-select></el-form-item>
        <el-form-item label="股息标的"><el-switch v-model="form.is_dividend_asset" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.note" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Search } from '@element-plus/icons-vue';
import { api, rows } from '@/api/client';
import type { Asset } from '@/api/types';
import { MARKET_LABELS, ASSET_TYPE_LABELS, ASSET_TYPES, CURRENCIES, marketLabel } from '@/api/format';

const assets = ref<Asset[]>([]);
const loading = ref(true);
const saving = ref(false);
const dialog = ref(false);
const search = ref('');
const form = reactive({ id: 0, symbol: '', name: '', market: 'A', asset_type: 'STOCK', currency: 'CNY', is_dividend_asset: false, note: '' });

async function load() {
  loading.value = true;
  try {
    const q = search.value ? `?search=${encodeURIComponent(search.value)}` : '';
    assets.value = rows(await api.get<{ results: Asset[] }>(`/assets/${q}`));
  } finally { loading.value = false; }
}
function openAdd() {
  Object.assign(form, { id: 0, symbol: '', name: '', market: 'A', asset_type: 'STOCK', currency: 'CNY', is_dividend_asset: false, note: '' });
  dialog.value = true;
}
function openEdit(a: Asset) {
  Object.assign(form, { id: a.id, symbol: a.symbol, name: a.name, market: a.market, asset_type: a.asset_type, currency: a.currency, is_dividend_asset: a.is_dividend_asset, note: a.note });
  dialog.value = true;
}
async function save() {
  if (!form.symbol.trim()) return ElMessage.warning('请填写代码');
  saving.value = true;
  try {
    const payload = { symbol: form.symbol, name: form.name, market: form.market, asset_type: form.asset_type, currency: form.currency, is_dividend_asset: form.is_dividend_asset, note: form.note };
    if (form.id) await api.patch(`/assets/${form.id}/`, payload);
    else await api.post('/assets/', payload);
    ElMessage.success('已保存');
    dialog.value = false;
    await load();
  } catch (e) { ElMessage.error((e as Error).message); }
  finally { saving.value = false; }
}
async function remove(a: Asset) {
  try {
    await ElMessageBox.confirm(`删除标的「${a.symbol}」？`, '确认', { type: 'warning' });
    await api.del(`/assets/${a.id}/`);
    ElMessage.success('已删除');
    await load();
  } catch (e) { if ((e as Error).message !== 'cancel') ElMessage.error((e as Error).message); }
}
onMounted(load);
</script>

<style scoped>
.mb { margin-bottom: 18px; }
</style>
