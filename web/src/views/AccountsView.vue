<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">账户管理</h2><div class="page-sub">账户按用户隔离；已有流水的账户不可删除</div></div>
      <el-button type="primary" @click="openAdd"><el-icon><Plus /></el-icon>新增账户</el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="accounts" v-loading="loading" style="width: 100%">
        <el-table-column prop="name" label="账户名称" min-width="150" />
        <el-table-column prop="broker" label="券商 / 机构" min-width="120" />
        <el-table-column label="市场" width="100"><template #default="{ row }"><el-tag size="small" effect="plain">{{ marketLabel(row.market) }}</el-tag></template></el-table-column>
        <el-table-column label="类型" width="120"><template #default="{ row }">{{ ACCOUNT_TYPE_LABELS[row.account_type] || row.account_type }}</template></el-table-column>
        <el-table-column prop="currency" label="币种" width="90" />
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!accounts.length && !loading" class="empty-block">还没有账户，点「新增账户」开始。</div>
    </el-card>

    <el-dialog v-model="dialog" :title="form.id ? '编辑账户' : '新增账户'" width="520px">
      <el-form label-position="top">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="券商 / 机构"><el-input v-model="form.broker" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="市场"><el-select v-model="form.market" style="width: 100%"><el-option v-for="(label, key) in MARKET_LABELS" :key="key" :label="label" :value="key" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="类型"><el-select v-model="form.account_type" style="width: 100%"><el-option v-for="key in ACCOUNT_TYPES" :key="key" :label="ACCOUNT_TYPE_LABELS[key]" :value="key" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="币种"><el-select v-model="form.currency" style="width: 100%"><el-option v-for="c in CURRENCIES" :key="c" :label="c" :value="c" /></el-select></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.note" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { api, rows } from '@/api/client';
import type { Account } from '@/api/types';
import { MARKET_LABELS, ACCOUNT_TYPE_LABELS, ACCOUNT_TYPES, CURRENCIES, marketLabel } from '@/api/format';

const accounts = ref<Account[]>([]);
const loading = ref(true);
const saving = ref(false);
const dialog = ref(false);
const form = reactive({ id: 0, name: '', broker: '', market: 'A', account_type: 'BROKER', currency: 'CNY', note: '' });

async function load() {
  loading.value = true;
  try { accounts.value = rows(await api.get<{ results: Account[] }>('/accounts/')); }
  finally { loading.value = false; }
}

function openAdd() {
  Object.assign(form, { id: 0, name: '', broker: '', market: 'A', account_type: 'BROKER', currency: 'CNY', note: '' });
  dialog.value = true;
}
function openEdit(a: Account) {
  Object.assign(form, { id: a.id, name: a.name, broker: a.broker, market: a.market, account_type: a.account_type, currency: a.currency, note: a.note });
  dialog.value = true;
}
async function save() {
  if (!form.name.trim()) return ElMessage.warning('请填写名称');
  saving.value = true;
  try {
    const payload = { name: form.name, broker: form.broker, market: form.market, account_type: form.account_type, currency: form.currency, note: form.note };
    if (form.id) await api.patch(`/accounts/${form.id}/`, payload);
    else await api.post('/accounts/', payload);
    ElMessage.success('已保存');
    dialog.value = false;
    await load();
  } catch (e) { ElMessage.error((e as Error).message); }
  finally { saving.value = false; }
}
async function remove(a: Account) {
  try {
    await ElMessageBox.confirm(`删除账户「${a.name}」？`, '确认', { type: 'warning' });
    await api.del(`/accounts/${a.id}/`);
    ElMessage.success('已删除');
    await load();
  } catch (e) { if ((e as Error).message !== 'cancel') ElMessage.error((e as Error).message); }
}
onMounted(load);
</script>
