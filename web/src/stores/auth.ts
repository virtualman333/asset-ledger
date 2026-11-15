import { reactive, computed } from 'vue';
import { token, clearSession, apiBase, setApiBase } from '@/api/client';

const state = reactive({
  token: token(),
  username: localStorage.getItem('al_web_username') || '',
  apiBase: apiBase(),
});

export const auth = {
  state,
  isLogin: computed(() => !!state.token),
  setUser(username: string) {
    state.username = username;
    localStorage.setItem('al_web_username', username);
  },
  syncToken() {
    state.token = token();
  },
  setBase(url: string) {
    setApiBase(url);
    state.apiBase = apiBase();
  },
  logout() {
    clearSession();
    localStorage.removeItem('al_web_username');
    state.token = '';
    state.username = '';
  },
};
