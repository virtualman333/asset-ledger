import { ref, watchEffect } from 'vue';

const saved = localStorage.getItem('al_web_theme');
export const isDark = ref(saved ? saved === 'dark' : false);

watchEffect(() => {
  document.documentElement.classList.toggle('dark', isDark.value);
  localStorage.setItem('al_web_theme', isDark.value ? 'dark' : 'light');
});

export function toggleTheme() {
  isDark.value = !isDark.value;
}
