<template>
  <!-- 分离式按钮组：按钮各自独立、细缝分隔，科技感调性 -->
  <div class="action-group" :class="{ subtle: subtle }">
    <slot />
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ subtle?: boolean }>(), { subtle: false });
</script>

<style scoped>
.action-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border-radius: 12px;
  background: var(--surface-2);
  border: 1px solid var(--line);
}
.action-group.subtle { background: transparent; border-color: transparent; }
.action-group :deep(.el-button) { margin: 0; }
.action-group :deep(.el-button + .el-button) { margin-left: 0; }
</style>
