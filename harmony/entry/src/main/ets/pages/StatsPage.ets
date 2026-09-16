import { ApiClient, fmt, pnlColor } from '../common/ApiClient';
import { DividendMonth, Summary } from '../common/Models';

/** 统计页：收益总览 + 股息月度。 */
@Component
export struct StatsPage {
  @State summary: Summary | undefined = undefined;
  @State months: DividendMonth[] = [];
  @State year: number = 2026;

  async aboutToAppear(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    try {
      this.summary = await ApiClient.get<Summary>('/analytics/summary/');
      const res = await ApiClient.get<{ months: DividendMonth[] }>(`/analytics/dividends/?year=${this.year}`);
      this.months = res.months;
    } catch (err) {
      console.error(`load stats failed: ${JSON.stringify(err)}`);
    }
  }

  @Builder
  row(label: string, value: string, color: string) {
    Row() {
      Text(label).fontSize(14).fontColor('#8A8A8A')
      Blank()
      Text(value).fontSize(15).fontColor(color)
    }.width('100%').height(40)
  }

  build() {
    Column({ space: 12 }) {
      Text('统计').fontSize(20).fontWeight(FontWeight.Medium).width('100%')

      if (this.summary) {
        Column({ space: 4 }) {
          this.row('持仓成本', fmt(this.summary.cost_basis), '#1A1A1A')
          this.row('市值', fmt(this.summary.market_value), '#1A1A1A')
          this.row('浮动盈亏', fmt(this.summary.unrealized_pnl), pnlColor(this.summary.unrealized_pnl))
          this.row('已实现盈亏', fmt(this.summary.realized_pnl), pnlColor(this.summary.realized_pnl))
          this.row('累计股息', fmt(this.summary.dividend_total), '#7166F0')
          this.row(
            '年化',
            this.summary.annualized === null ? '待计算' : `${(this.summary.annualized * 100).toFixed(2)}%`,
            '#1A1A1A'
          )
          // 年化旁边那句解释以前算出来了却没显示：用户只看到「待计算」，
          // 不知道是现金流不够、还是有持仓拿不到报价
          if (this.summary.valuation_note) {
            Text(this.summary.valuation_note).fontSize(11).fontColor('#8A8A8A').width('100%')
          }
        }
        .width('100%')
        .padding(16)
        .borderRadius(12)
        .backgroundColor('#FFFFFF')
      }

      Row() {
        Text(`${this.year} 年股息`).fontSize(15).fontWeight(FontWeight.Medium)
        Blank()
        Text('刷新').fontSize(13).fontColor('#7166F0').onClick(() => {
          this.load();
        })
      }.width('100%').margin({ top: 8 })

      List({ space: 8 }) {
        ForEach(this.months, (item: DividendMonth) => {
          ListItem() {
            Row() {
              Text(item.month).fontSize(14)
              Blank()
              Text(fmt(item.net)).fontSize(14).fontColor('#7166F0')
              Text(`${item.count} 笔`).fontSize(12).fontColor('#8A8A8A').margin({ left: 12 })
            }
            .width('100%')
            .padding(12)
            .borderRadius(10)
            .backgroundColor('#FFFFFF')
          }
        }, (item: DividendMonth) => item.month)
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
