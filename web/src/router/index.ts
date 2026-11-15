import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { token } from '@/api/client';

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/components/AppLayout.vue'),
    redirect: '/holdings',
    children: [
      { path: 'holdings', name: 'holdings', component: () => import('@/views/HoldingsView.vue'), meta: { title: '持仓' } },
      { path: 'record', name: 'record', component: () => import('@/views/RecordView.vue'), meta: { title: '记一笔' } },
      { path: 'inbox', name: 'inbox', component: () => import('@/views/InboxView.vue'), meta: { title: '收件箱' } },
      { path: 'stats', name: 'stats', component: () => import('@/views/StatsView.vue'), meta: { title: '统计' } },
      { path: 'transactions', name: 'transactions', component: () => import('@/views/TransactionsView.vue'), meta: { title: '流水明细' } },
      { path: 'accounts', name: 'accounts', component: () => import('@/views/AccountsView.vue'), meta: { title: '账户管理' } },
      { path: 'assets', name: 'assets', component: () => import('@/views/AssetsView.vue'), meta: { title: '标的库' } },
      { path: 'dividends', name: 'dividends', component: () => import('@/views/DividendsView.vue'), meta: { title: '股息明细' } },
      { path: 'calendar', name: 'calendar', component: () => import('@/views/CalendarView.vue'), meta: { title: '股息日历' } },
      { path: 'quotes', name: 'quotes', component: () => import('@/views/QuotesView.vue'), meta: { title: '行情与汇率' } },
      { path: 'import', name: 'import', component: () => import('@/views/ImportView.vue'), meta: { title: '导入流水' } },
      { path: 'draft/:id', name: 'draft', component: () => import('@/views/DraftEditView.vue'), meta: { title: '核对草稿' } },
      { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '设置' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  if (!to.meta.public && !token()) return { path: '/login', query: { redirect: to.fullPath } };
  if (to.path === '/login' && token()) return { path: '/holdings' };
  return true;
});
