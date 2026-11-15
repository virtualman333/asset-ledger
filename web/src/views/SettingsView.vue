<template>
  <div style="max-width: 720px">
    <div class="page-head"><div><h2 class="page-title">设置</h2><div class="page-sub">后端地址与主题</div></div></div>

    <el-card shadow="never" class="mb">
      <template #header><span class="card-title">后端服务</span></template>
      <el-form label-position="top">
        <el-form-item label="API 基址（生产与页面同源，留空走 /api/v1）">
          <el-input v-model="base" placeholder="https://fiance.virtualman.top/api/v1" />
        </el-form-item>
        <el-button type="primary" @click="saveBase">保存地址</el-button>
      </el-form>
      <el-alert :title="`当前：${auth.state.apiBase}`" type="info" :closable="false" class="mt" />
    </el-card>

    <el-card shadow="never">
      <template #header><span class="card-title">外观</span></template>
      <div class="row-between">
        <span>深色模式</span>
        <el-switch v-model="isDark" @change="toggleTheme" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { auth } from '@/stores/auth';
import { isDark, toggleTheme } from '@/stores/theme';

const base = ref(auth.state.apiBase);

function saveBase() {
  auth.setBase(base.value);
  ElMessage.success('已保存，刷新后生效');
}
</script>

<style scoped>
.mb { margin-bottom: 18px; }
.mt { margin-top: 14px; }
.row-between { display: flex; justify-content: space-between; align-items: center; }
.card-title { font-weight: 600; }
</style>
