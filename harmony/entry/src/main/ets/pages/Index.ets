import { ApiClient } from '../common/ApiClient';
import { HoldingsPage } from './HoldingsPage';
import { InboxPage } from './InboxPage';
import { LoginView } from './LoginView';
import { RecordPage } from './RecordPage';
import { StatsPage } from './StatsPage';

@Entry
@Component
struct Index {
  @State loggedIn: boolean = false;
  @State booting: boolean = true;

  async aboutToAppear(): Promise<void> {
    await ApiClient.restore();
    this.loggedIn = ApiClient.token.length > 0;
    this.booting = false;
  }

  build() {
    Column() {
      if (this.booting) {
        LoadingProgress().width(36).height(36)
      } else if (this.loggedIn) {
        MainTabs({ onLogout: () => {
          this.loggedIn = false;
        } })
      } else {
        LoginView({ onSuccess: () => {
          this.loggedIn = true;
        } })
      }
    }
    .width('100%')
    .height('100%')
  }
}

@Component
struct MainTabs {
  @State current: number = 0;
  onLogout: () => void = () => {
  };

  build() {
    Tabs({ barPosition: BarPosition.End, index: this.current }) {
      TabContent() {
        HoldingsPage()
      }.tabBar(this.tabItem('持仓', 0))

      TabContent() {
        RecordPage()
      }.tabBar(this.tabItem('记一笔', 1))

      TabContent() {
        InboxPage()
      }.tabBar(this.tabItem('收件箱', 2))

      TabContent() {
        StatsPage()
      }.tabBar(this.tabItem('统计', 3))

      TabContent() {
        Column({ space: 16 }) {
          Text('我的').fontSize(20).fontWeight(FontWeight.Medium)
          Button('退出登录')
            .width('100%')
            .backgroundColor('#B4B2A9')
            .onClick(() => {
              ApiClient.clear();
              this.onLogout();
            })
        }
        .width('100%')
        .height('100%')
        .padding(16)
        .backgroundColor('#F7F7F9')
      }.tabBar(this.tabItem('我的', 4))
    }
    .barHeight(56)
    .onChange((index: number) => {
      this.current = index;
    })
  }

  @Builder
  tabItem(title: string, index: number) {
    Column() {
      Text(title)
        .fontSize(13)
        .fontColor(this.current === index ? '#7166F0' : '#8A8A8A')
    }.width('100%').height('100%').justifyContent(FlexAlign.Center)
  }
}
