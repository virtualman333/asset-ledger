<template>
  <el-container class="shell">
    <el-aside :width="collapsed ? '76px' : '228px'" class="aside">
      <div class="brand" @click="router.push('/holdings')">
        <div class="brand-mark">息</div>
        <div v-if="!collapsed" class="brand-text">
          <strong>股息账本</strong>
          <span>ASSET LEDGER</span>
        </div>
      </div>

      <el-menu :default-active="route.path" :collapse="collapsed" router class="menu">
        <el-menu-item index="/holdings"><el-icon><DataAnalysis /></el-icon><span>持仓总览</span></el-menu-item>
        <el-menu-item index="/record"><el-icon><EditPen /></el-icon><span>记一笔</span></el-menu-item>
        <el-menu-item index="/inbox"><el-icon><MessageBox /></el-icon><span>收件箱</span></el-menu-item>
        <el-menu-item index="/stats"><el-icon><TrendCharts /></el-icon><span>统计</span></el-menu-item>
        <el-menu-item index="/transactions"><el-icon><List /></el-icon><span>流水明细</span></el-menu-item>
        <el-menu-item index="/dividends"><el-icon><Money /></el-icon><span>股息明细</span></el-menu-item>
        <el-menu-item index="/calendar"><el-icon><Calendar /></el-icon><span>股息日历</span></el-menu-item>
        <el-menu-item index="/quotes"><el-icon><Coin /></el-icon><span>行情与汇率</span></el-menu-item>
        <el-divider style="margin: 12px 16px" />
        <el-menu-item index="/accounts"><el-icon><Wallet /></el-icon><span>账户管理</span></el-menu-item>
        <el-menu-item index="/assets"><el-icon><Files /></el-icon><span>标的库</span></el-menu-item>
        <el-menu-item index="/import"><el-icon><UploadFilled /></el-icon><span>导入流水</span></el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon><span>设置</span></el-menu-item>
      </el-menu>

      <div class="aside-foot" v-if="!collapsed">
        <span class="dot" /> 数据由 Django 后端驱动
      </div>
    </el-aside>

    <el-container class="main">
      <el-header class="header">
        <div class="header-left">
          <el-button text circle @click="collapsed = !collapsed" aria-label="折叠侧栏">
            <el-icon><Expand v-if="collapsed" /><Fold v-else /></el-icon>
          </el-button>
          <span class="crumb">{{ (route.meta.title as string) || '股息账本' }}</span>
        </div>
        <div class="header-right">
          <el-button text circle @click="toggleTheme" :aria-label="isDark ? '切换亮色' : '切换暗色'">
            <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
          </el-button>
          <el-tag type="info" effect="plain" size="small" class="base-tag">{{ auth.state.apiBase }}</el-tag>
          <el-dropdown @command="onCommand">
            <span class="user-chip">
              <span class="avatar">{{ (auth.state.username || 'U').slice(0, 1).toUpperCase() }}</span>
              <span class="uname">{{ auth.state.username || '未登录' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="settings">设置</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="content">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessageBox } from 'element-plus';
import {
  DataAnalysis, EditPen, MessageBox, TrendCharts, List, Money, Calendar, Coin,
  Wallet, Files, UploadFilled, Setting, Expand, Fold, Moon, Sunny, ArrowDown,
} from '@element-plus/icons-vue';
import { auth } from '@/stores/auth';
import { isDark, toggleTheme } from '@/stores/theme';

const route = useRoute();
const router = useRouter();
const collapsed = ref(false);

function onCommand(cmd: string) {
  if (cmd === 'settings') router.push('/settings');
  if (cmd === 'logout') {
    ElMessageBox.confirm('清除本机登录凭证？账本数据仍在服务器。', '退出登录', { type: 'warning' })
      .then(() => {
        auth.logout();
        router.push('/login');
      })
      .catch(() => {});
  }
}
</script>

<style scoped>
.shell { min-height: 100vh; background: transparent; }
.aside {
  position: sticky;
  top: 0;
  height: 100vh;
  background: var(--surface);
  backdrop-filter: blur(18px);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  transition: width .2s ease;
  z-index: 10;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 22px 20px 18px; cursor: pointer; overflow: hidden; }
.brand-mark {
  flex: none;
  width: 38px; height: 38px; border-radius: 11px;
  display: grid; place-items: center;
  background: linear-gradient(135deg, #7166f0, #9a8cff);
  color: #fff; font-weight: 700; font-size: 18px;
  box-shadow: 0 6px 18px rgba(113, 102, 240, .35);
}
.brand-text { display: flex; flex-direction: column; line-height: 1.25; white-space: nowrap; }
.brand-text strong { font-size: 16px; letter-spacing: .01em; }
.brand-text span { font-size: 10px; color: var(--muted); letter-spacing: .16em; margin-top: 3px; }
.menu { flex: 1; border-right: none; padding: 0 10px; background: transparent; }
.aside-foot { padding: 16px 20px; font-size: 11px; color: var(--muted); white-space: nowrap; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--down); margin-right: 6px; }

.main { min-width: 0; background: transparent; }
.header {
  position: sticky; top: 0; z-index: 9;
  height: 64px;
  display: flex; align-items: center; justify-content: space-between;
  background: var(--surface);
  backdrop-filter: blur(18px);
  border-bottom: 1px solid var(--line);
}
.header-left, .header-right { display: flex; align-items: center; gap: 10px; }
.crumb { font-size: 15px; font-weight: 600; margin-left: 6px; }
.base-tag { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-chip { display: flex; align-items: center; gap: 8px; cursor: pointer; outline: none; }
.avatar {
  width: 30px; height: 30px; border-radius: 9px;
  background: var(--brand-soft); color: var(--brand);
  display: grid; place-items: center; font-weight: 650; font-size: 13px;
}
.uname { font-size: 13px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.content { padding: 26px 30px 60px; max-width: 1440px; width: 100%; margin: 0 auto; }

@media (max-width: 768px) {
  .aside { position: fixed; }
  .content { padding: 18px 16px 48px; }
  .base-tag, .uname { display: none; }
}
</style>
