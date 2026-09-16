import { ApiClient, fmt, pnlColor } from '../common/ApiClient';
import { Position, Summary } from '../common/Models';

/** 持仓页：总览卡片 + 持仓列表，下拉/按钮均可刷新。 */
@Component
export struct HoldingsPage {
  @State summary: Summary | undefined = undefined;
  @State positions: Position[] = [];
  @State loading: boolean = false;
  @State error: string = '';

  async aboutToAppear(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.error = '';
    try {
      const summary = await ApiClient.get<Summary>('/analytics/summary/');
      const positions = await ApiClient.get<{ results: Position[] }>('/analytics/positions/');
      this.summary = summary;
      this.positions = positions.results;
    } catch (err) {
      this.error = err instanceof Error ? err.message : JSON.stringify(err);
    } finally {
      this.loading = false;
    }
  }

  @Builder
  summaryCard() {
    if (this.summary) {
      Column({ space: 8 }) {
        Text(`总资产（${this.summary.base_currency}）`)
          .fontSize(13)
          .fontColor('#8A8A8A')
        Text(fmt(this.summary.market_value))
          .fontSize(30)
          .fontWeight(FontWeight.Medium)
        Row({ space: 24 }) {
          Column({ space: 2 }) {
            Text('浮动盈亏').fontSize(12).fontColor('#8A8A8A')
            Text(fmt(this.summary.unrealized_pnl))
              .fontSize(15)
              .fontColor(pnlColor(this.summary.unrealized_pnl))
          }
          Column({ space: 2 }) {
            Text('已实现').fontSize(12).fontColor('#8A8A8A')
            Text(fmt(this.summary.realized_pnl))
              .fontSize(15)
              .fontColor(pnlColor(this.summary.realized_pnl))
          }
          Column({ space: 2 }) {
            Text('累计股息').fontSize(12).fontColor('#8A8A8A')
            Text(fmt(this.summary.dividend_total)).fontSize(15).fontColor('#7166F0')
          }
        }.width('100%').justifyContent(FlexAlign.SpaceBetween)

        if (this.summary.annualized !== null && this.summary.annualized !== undefined) {
          Text(`年化 ${(this.summary.annualized * 100).toFixed(2)}%`)
            .fontSize(12)
            .fontColor('#8A8A8A')
        }
        // 「总资产 0.00」不能就这么摆着：没报价的持仓按成本算在下面这句话里说清楚
        if (this.summary.valuation_note) {
          Text(this.summary.valuation_note).fontSize(11).fontColor('#BA7517').width('100%')
        }
        if (this.summary.fx_warning) {
          Text('部分币种缺少汇率，折算可能不准').fontSize(11).fontColor('#BA7517')
        }
      }
      .width('100%')
      .padding(20)
      .borderRadius(16)
      .backgroundColor('#F5F4FE')
    }
  }

  build() {
    Column({ space: 12 }) {
      Row() {
        Text('持仓').fontSize(20).fontWeight(FontWeight.Medium)
        Blank()
        Button('刷新')
          .fontSize(13)
          .height(30)
          .backgroundColor('#7166F0')
          .onClick(() => {
            this.load();
          })
      }.width('100%')

      if (this.error.length > 0) {
        Text(this.error).fontSize(12).fontColor('#E24B4A').width('100%')
      }

      this.summaryCard()

      if (this.positions.length === 0 && !this.loading) {
        Text('还没有持仓，先记一笔买入吧').fontSize(13).fontColor('#8A8A8A').margin({ top: 40 })
      }

      List({ space: 10 }) {
        ForEach(this.positions, (item: Position) => {
          ListItem() {
            Row({ space: 12 }) {
              Column({ space: 4 }) {
                Text(item.symbol).fontSize(16).fontWeight(FontWeight.Medium)
                Text(item.name).fontSize(12).fontColor('#8A8A8A')
                Text(`${fmt(item.quantity, 4)} @ ${fmt(item.avg_cost)}`)
                  .fontSize(12)
                  .fontColor('#8A8A8A')
              }.alignItems(HorizontalAlign.Start).layoutWeight(1)

              Column({ space: 4 }) {
                Text(fmt(item.market_value)).fontSize(16)
                Text(fmt(item.unrealized_pnl))
                  .fontSize(13)
                  .fontColor(pnlColor(item.unrealized_pnl))
              }.alignItems(HorizontalAlign.End)
            }
            .width('100%')
            .padding(14)
            .borderRadius(12)
            .backgroundColor('#FFFFFF')
          }
        }, (item: Position) => `${item.asset_id}-${item.symbol}`)
      }
      .width('100%')
      .layoutWeight(1)
    }
    .width('100%')
    .height('100%')
    .padding(16)
    .backgroundColor('#F7F7F9')
  }
}
