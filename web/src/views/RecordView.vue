<template>
  <div>
    <div class="page-head">
      <div><h2 class="page-title">记一笔</h2><div class="page-sub">标的不存在时按「市场+代码」自动创建并复用</div></div>
    </div>

    <div class="grid">
      <el-card class="form-card" shadow="never">
        <div class="section-label">交易方向</div>
        <div class="side-row">
          <el-button v-for="(label, i) in sideButtons" :key="label" :type="sideIndex === i ? 'primary' : 'default'"
            :plain="sideIndex !== i" @click="applySide(i)">{{ label }}</el-button>
        </div>

        <el-form label-position="top" class="form">
          <el-form-item label="账户">
            <el-select v-model="accountId" placeholder="选择账户" style="width: 100%" filterable>
              <el-option v-for="a in accounts" :key="a.id" :label="`${a.name}（${a.currency}）`" :value="a.id" />
            </el-select>
          </el-form-item>

          <template v-if="needAsset">
            <el-form-item label="标的代码">
              <el-input v-model="symbol" placeholder="如 600519 / AAPL / BTC" @blur="findAsset" />
            </el-form-item>
            <el-form-item label="标的名称"><el-input v-model="assetName" placeholder="可留空" /></el-form-item>
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="市场">
                  <el-select v-model="market" style="width: 100%" @change="onMarketChange">
                    <el-option v-for="(label, key) in MARKET_LABELS" :key="key" :label="label" :value="key" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="币种">
                  <el-select v-model="currency" style="width: 100%">
                    <el-option v-for="c in CURRENCIES" :key="c" :label="c" :value="c" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
          </template>

          <template v-if="side === 'DIVIDEND'">
            <el-row :gutter="12">
              <el-col :span="12"><el-form-item label="每股派息"><el-input-number v-model="perShare" :min="0" :precision="4" style="width: 100%" /></el-form-item></el-col>
              <el-col :span="12"><el-form-item label="持股数"><el-input-number v-model="shares" :min="0" :precision="4" style="width: 100%" /></el-form-item></el-col>
            </el-row>
            <el-form-item label="到手净额（留空按税前-税费）"><el-input-number v-model="net" :min="0" :precision="2" style="width: 100%" /></el-form-item>
          </template>

          <template v-if="needAmount">
            <el-form-item label="金额"><el-input-number v-model="amount" :min="0" :precision="2" style="width: 100%" /></el-form-item>
          </template>

          <template v-if="side === 'BUY' || side === 'SELL'">
            <el-row :gutter="12">
              <el-col :span="12"><el-form-item label="数量"><el-input-number v-model="quantity" :min="0" :precision="4" style="width: 100%" /></el-form-item></el-col>
              <el-col :span="12"><el-form-item label="单价"><el-input-number v-model="price" :min="0" :precision="4" style="width: 100%" /></el-form-item></el-col>
            </el-row>
          </template>

          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="手续费"><el-input-number v-model="fee" :min="0" :precision="2" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="税费"><el-input-number v-model="tax" :min="0" :precision="2" style="width: 100%" /></el-form-item></el-col>
          </el-row>

          <el-row :gutter="12">
            <el-col :span="14"><el-form-item label="日期"><el-date-picker v-model="tradedDate" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="10"><el-form-item label="时间"><el-time-picker v-model="tradedTime" format="HH:mm" value-format="HH:mm" style="width: 100%" /></el-form-item></el-col>
          </el-row>

          <el-form-item label="备注"><el-input v-model="note" placeholder="可选" /></el-form-item>
        </el-form>

        <el-alert :title="preview" type="info" :closable="false" class="mb" />
        <el-button type="primary" size="large" style="width: 100%" :loading="submitting" @click="submit">确认入账</el-button>
      </el-card>

      <div>
        <el-card shadow="never" class="mb">
          <template #header><span class="card-title">现金口径</span></template>
          <ul class="rules">
            <li>买入 = −(数量×单价 + 手续费 + 税费)</li>
            <li>卖出 = 数量×单价 − 手续费 − 税费</li>
            <li>入金 / 出金必须显式填写金额</li>
            <li>股息走明细接口，金额为税后净额</li>
          </ul>
        </el-card>
        <el-card shadow="never">
          <template #header><span class="card-title">设计红线</span></template>
          <ul class="rules">
            <li>流水是唯一事实源</li>
            <li>AI 只生成草稿，人确认才入账</li>
            <li>原始凭证永不硬删</li>
          </ul>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { api, rows } from '@/api/client';
import type { Account, Asset } from '@/api/types';
import { SIDE_LABELS, SIDES, MARKET_LABELS, CURRENCIES, money, today, currencyForMarket } from '@/api/format';

const SIDE_VALUES = SIDES;
const sideButtons = SIDES.map((s) => SIDE_LABELS[s]);
const accounts = ref<Account[]>([]);
const accountId = ref<number | null>(null);
const sideIndex = ref(0);
const side = computed(() => SIDE_VALUES[sideIndex.value]);
const needAsset = computed(() => ['BUY', 'SELL', 'DIVIDEND'].includes(side.value));
const needAmount = computed(() => ['DEPOSIT', 'WITHDRAW'].includes(side.value));

const symbol = ref('');
const assetName = ref('');
const market = ref('A');
const currency = ref('CNY');
const assetId = ref<number | null>(null);
const quantity = ref<number | null>(null);
const price = ref<number | null>(null);
const amount = ref<number | null>(null);
const perShare = ref<number | null>(null);
const shares = ref<number | null>(null);
const net = ref<number | null>(null);
const fee = ref(0);
const tax = ref(0);
const note = ref('');
const tradedDate = ref(today());
const tradedTime = ref('09:30');
const submitting = ref(false);

const preview = computed(() => {
  if (needAmount.value) return `${SIDE_LABELS[side.value]} ${money(amount.value)} ${currency.value}`;
  if (side.value === 'DIVIDEND') {
    const gross = (perShare.value || 0) * (shares.value || 0);
    const netValue = net.value === null ? gross - (tax.value || 0) : net.value;
    return `股息 · 税前 ${money(gross)} / 到手 ${money(netValue)}`;
  }
  const gross = (quantity.value || 0) * (price.value || 0);
  const cash = side.value === 'BUY' ? -(gross + (fee.value || 0) + (tax.value || 0)) : gross - (fee.value || 0) - (tax.value || 0);
  return `${SIDE_LABELS[side.value]} ${symbol.value || '—'} · 现金变动 ${money(cash)} ${currency.value}`;
});

function applySide(i: number) {
  sideIndex.value = i;
}

function onMarketChange(m: string) {
  currency.value = currencyForMarket(m);
}

async function loadAccounts() {
  const data = await api.get<{ results: Account[] }>('/accounts/');
  accounts.value = rows(data);
  if (!accountId.value && accounts.value.length) accountId.value = accounts.value[0].id;
}

async function findAsset() {
  if (!symbol.value.trim()) return;
  const data = await api.get<{ results: Asset[] }>(`/assets/?search=${encodeURIComponent(symbol.value.trim())}`);
  const exact = rows(data).find((a) => a.symbol === symbol.value.trim() && a.market === market.value);
  if (exact) {
    assetId.value = exact.id;
    assetName.value = exact.name;
  } else {
    assetId.value = null;
  }
}

async function resolveAsset(): Promise<number | null> {
  if (assetId.value) return assetId.value;
  const data = await api.get<{ results: Asset[] }>(`/assets/?search=${encodeURIComponent(symbol.value.trim())}`);
  const exact = rows(data).find((a) => a.symbol === symbol.value.trim() && a.market === market.value);
  if (exact) return exact.id;
  const created = await api.post<Asset>('/assets/', {
    symbol: symbol.value.trim(),
    name: assetName.value || symbol.value.trim(),
    market: market.value,
    currency: currency.value,
    is_dividend_asset: side.value === 'DIVIDEND',
  });
  assetId.value = created.id;
  return created.id;
}

async function submit() {
  if (!accountId.value) return ElMessage.warning('请先创建账户');
  if (needAsset.value && !symbol.value.trim()) return ElMessage.warning('请填写标的代码');
  if (needAmount.value && !amount.value) return ElMessage.warning('入金 / 出金必须填写金额');
  submitting.value = true;
  try {
    const tradedAt = `${tradedDate.value}T${tradedTime.value}:00`;
    if (side.value === 'DIVIDEND') {
      const asset = await resolveAsset();
      const grossValue = (perShare.value || 0) * (shares.value || 0);
      await api.post('/transactions/dividends/', {
        asset,
        account: accountId.value,
        pay_date: tradedDate.value,
        amount_per_share: perShare.value,
        shares: shares.value,
        gross: grossValue,
        tax: tax.value || 0,
        net: net.value === null ? grossValue - (tax.value || 0) : net.value,
        currency: currency.value,
      });
    } else {
      const asset = needAsset.value ? await resolveAsset() : null;
      const payload: Record<string, unknown> = {
        account: accountId.value,
        asset,
        side: side.value,
        currency: currency.value,
        traded_at: tradedAt,
        fee: fee.value || 0,
        tax: tax.value || 0,
        note: note.value,
        client_request_id: `web-${Date.now()}-${Math.floor(Math.random() * 10000)}`,
      };
      if (needAmount.value) payload.amount = Math.abs(amount.value || 0);
      else { payload.quantity = quantity.value || 0; payload.price = price.value || 0; }
      await api.post('/transactions/records/', payload);
    }
    ElMessage.success('已记录');
    quantity.value = null; price.value = null; amount.value = null; note.value = '';
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    submitting.value = false;
  }
}

onMounted(loadAccounts);
</script>

<style scoped>
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 20px; align-items: start; }
.section-label { font-size: 13px; color: var(--text-soft); margin-bottom: 10px; font-weight: 600; }
.side-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
.side-row .el-button { margin: 0; }
.form { max-width: 100%; }
.card-title { font-weight: 600; }
.rules { margin: 0; padding-left: 18px; color: var(--text-soft); font-size: 13px; line-height: 2; }
.mb { margin-bottom: 18px; }
@media (max-width: 980px) { .grid { grid-template-columns: 1fr; } }
</style>
