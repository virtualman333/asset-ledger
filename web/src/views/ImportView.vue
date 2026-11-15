<template>
  <div style="max-width: 900px">
    <div class="page-head"><div><h2 class="page-title">导入流水 CSV</h2><div class="page-sub">列名与「导出流水 CSV」一致；文件需为 UTF-8 编码</div></div></div>

    <el-card shadow="never" class="mb">
      <el-form label-position="top">
        <el-form-item label="选择 CSV 文件">
          <input ref="fileInput" type="file" accept=".csv,text/csv" @change="onFile" />
        </el-form-item>
        <el-form-item label="或直接粘贴 CSV 文本">
          <el-input v-model="csv" type="textarea" :rows="8" placeholder="时间,方向,账户,币种&#10;2026-11-15 09:30:00,买入,证券账户,CNY" />
        </el-form-item>
        <el-row :gutter="14">
          <el-col :span="12"><el-form-item label="幂等前缀（重复导入不会记两遍）"><el-input v-model="prefix" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="校验模式"><el-switch v-model="dryRun" /><span class="soft" style="margin-left: 10px; font-size: 12px">开启时只校验不落库</span></el-form-item></el-col>
        </el-row>
        <el-button type="primary" size="large" :loading="loading" @click="submit">{{ dryRun ? '校验' : '导入' }}</el-button>
      </el-form>
    </el-card>

    <el-card v-if="result" shadow="never">
      <template #header><span class="card-title">{{ result.headline }}</span></template>
      <el-table :data="result.failed" v-if="result.failed.length" style="width: 100%">
        <el-table-column prop="line" label="文件行号" width="110" />
        <el-table-column prop="error" label="错误原因" />
      </el-table>
      <div v-else class="empty-block">没有失败行。</div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { api } from '@/api/client';

const csv = ref('');
const prefix = ref(`web-import-${Date.now()}`);
const dryRun = ref(true);
const loading = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const result = ref<{ headline: string; failed: { line: number; error: string }[] } | null>(null);

function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => { csv.value = String(reader.result || ''); };
  reader.readAsText(file, 'UTF-8');
}

async function submit() {
  if (!csv.value.trim()) return ElMessage.warning('请选择或粘贴 CSV 内容');
  loading.value = true;
  try {
    const d = await api.post<{ ok: boolean; dry_run: boolean; total: number; created: number; skipped: number; failed: { line: number; error: string }[] }>('/transactions/records/import/', {
      csv: csv.value,
      dry_run: dryRun.value,
      client_request_id_prefix: prefix.value || `web-import-${Date.now()}`,
    });
    result.value = {
      headline: `${d.dry_run ? '校验结果' : '导入结果'}：共 ${d.total} 行，成功 ${d.created}，跳过 ${d.skipped}，失败 ${d.failed.length}`,
      failed: d.failed,
    };
  } catch (e) { ElMessage.error((e as Error).message); }
  finally { loading.value = false; }
}
</script>

<style scoped>
.mb { margin-bottom: 18px; }
.card-title { font-weight: 600; }
input[type=file] { font-size: 13px; color: var(--text-soft); }
</style>
