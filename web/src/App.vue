<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import { auth } from '@/stores/auth';
import { api } from '@/api/client';

onMounted(() => {
  if (auth.isLogin.value) {
    api.me<{ username: string }>().then((u) => auth.setUser(u.username)).catch(() => {});
  }
});
</script>
