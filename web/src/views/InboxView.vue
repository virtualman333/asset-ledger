<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">收件箱</h2><div class="page-sub">成交短信或截图交给 Agent 识别，确认才入账</div></div>
      <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
    </div>

    <el-card shadow="never" class="mb">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="成交短信 / 识别文本">
          <el-input v-model="text" type="textarea" :rows="3" placeholder="例如：买入 贵州茅台 600519 数量 100 价格 1520.00 手续费 5.00" />
        </el-form-item>
      </el-form>
      <ActionButtonGroup>
        <el-button type="primary" :loading="submitting" @click="submitText"><el-icon><MagicStick /></el-icon>识别文本</el-button>
        <el-button :loading="submitting" @click="openUpload"><el-icon><Picture /></el-icon>上传截图</el-button>
      </ActionButtonGroup>
      <input ref="fileInput" type="file" accept="image/*" hidden @change="onFile" />
      <div v-if="error" class="soft" style="color: var(--up); font-size: 13px; margin-top: 12px">{{ error }}</div>
    </el-card>

    <div class="page-head">
      <h3 class="page-title" style="font-size: 17px">草稿箱（{{ drafts.length }}）</h3>
    </div>

    <el-row :gutter="16" v-if="drafts.length">
      <el-col v-for="d in drafts" :key="d.id" :span="24" class="mb">
        <el-card shadow="never">
          <div class="draft-head">
            <div>
              <strong style="font-size: 16px">{{ d.sideLabel }} {{ d.symbol }}</strong>
              <div class="soft" style="font-size: 12px; margin-top: 4px">
                {{ d.kind }} · 置信度 {{ d.confidence }} · {{ d.createdAt }}
              </div>
            </div>
            <ActionButtonGroup subtle>
              <el-button type="primary" size="small" @click="router.push(`/draft/${d.id}`)">核对并确认</el-button>
              <el-button v-if="d.evidenceUrl" size="small" @click="previewEvidence(d.evidenceUrl)">凭证</el-button>
              <el-button type="danger" size="small" plain @click="discard(d.id)">丢弃</el-button>
            </ActionButtonGroup>
          </div>
          <div class="draft-meta">
            <span class="tagline tabular">{{ d.quantity }} × {{ d.price }} {{ d.currency }}</span>
            <el-tag :type="d.missing.length ? 'warning' : 'success'" size="small" effect="plain">
              {{ d.missing.length ? `待补：${d.missing.join('、')}` : '信息完整' }}
            </el-tag>
          </div>
          <div v-if="d.rawText" class="raw soft">{{ d.rawText }}</div>
        </el-card>
      </el-col>
    </el-row>
    <div v-else class="empty-block glass-card">草稿箱是空的。</div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { api, apiBase } from '@/api/client';
import type { Draft } from '@/api/types';
import { pct, datetime, sideLabel, num, money } from '@/api/format';
import ActionButtonGroup from '@/components/ActionButtonGroup.vue';

interface Row { id: number; sideLabel: string; symbol: string; kind: string; confidence: string; createdAt: string; quantity: string; price: string; currency: string; missing: string[]; evidenceUrl: string; rawText: string }

const router = useRouter();
const loading = ref(true);
const submitting = ref(false);
const text = ref('');
const error = ref('');
const fileInput = ref<HTMLInputElement | null>(null);
const drafts = ref<Row[]>([]);

function mapDraft(d: Draft): Row {
  const r = d.result || {};
  return {
    id: d.id,
    sideLabel: sideLabel(String(r.side || '')),
    symbol: String(r.symbol || '未识别'),
    kind: d.kind === 'IMAGE' ? '截图' : '文本',
    confidence: pct(d.confidence, 0),
    createdAt: datetime(d.created_at),
    quantity: num(r.quantity, 4),
    price: money(r.price, 4),
    currency: String(r.currency || 'CNY'),
    missing: d.missing || [],
    evidenceUrl: d.evidence_url || '',
    rawText: d.raw_text || '',
  };
}

async function load() {
  loading.value = true;
  try {
    const data = await api.get<{ results: Draft[] }>('/ingest/drafts/');
    drafts.value = data.results.map(mapDraft);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

async function submitText() {
  if (!text.value.trim()) return ElMessage.warning('请输入文本');
  submitting.value = true;
  try {
    const d = await api.post<Draft>('/ingest/text/', { text: text.value.trim(), client_request_id: `web-text-${Date.now()}` });
    text.value = '';
    ElMessage.success(d.status === 'SUCCEEDED' ? '已生成草稿' : (d.error || '未能识别'));
    await load();
  } catch (e) { error.value = (e as Error).message; }
  finally { submitting.value = false; }
}

function openUpload() { fileInput.value?.click(); }

async function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  submitting.value = true;
  try {
    await api.upload('/ingest/image/', file);
    ElMessage.success('识别完成，请在草稿箱核对');
    await load();
  } catch (err) { error.value = (err as Error).message; }
  finally {
    submitting.value = false;
    (e.target as HTMLInputElement).value = '';
  }
}

async function discard(id: number) {
  try {
    await ElMessageBox.confirm('丢弃草稿？原始凭证会保留。', '确认', { type: 'warning' });
    await api.post(`/ingest/drafts/${id}/discard/`, {});
    ElMessage.success('已丢弃');
    await load();
  } catch { /* canceled */ }
}

function previewEvidence(url: string) {
  window.open(url.startsWith('http') ? url : `${apiBase().replace(/\/api\/v1$/, '')}${url}`, '_blank');
}

onMounted(load);
</script>

<style scoped>
.mb { margin-bottom: 16px; }
.draft-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.draft-meta { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
.tagline { font-weight: 600; }
.raw { margin-top: 12px; padding: 10px 12px; background: var(--surface-2); border-radius: 8px; font-size: 12px; line-height: 1.6; white-space: pre-wrap; }
</style>
