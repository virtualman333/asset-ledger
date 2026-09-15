import { promptAction } from '@kit.ArkUI';
import { ApiClient } from '../common/ApiClient';

interface AccountItem {
  id: number;
  name: string;
  currency: string;
  market: string;
}

interface AssetItem {
  id: number;
  symbol: string;
  name: string;
  market: string;
  currency: string;
}

interface ListResponse<T> {
  count: number;
  results: T[];
}

const MARKETS: string[] = ['A', 'HK', 'US', 'FUND', 'CRYPTO', 'FOREX', 'OTHER'];
const SIDES: string[] = ['BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW'];

function marketLabel(market: string): string {
  switch (market) {
    case 'A':
      return 'A股';
    case 'HK':
      return '港股';
    case 'US':
      return '美股';
    case 'FUND':
      return '基金';
    case 'CRYPTO':
      return '数字货币';
    case 'FOREX':
      return '外汇';
    default:
      return '其他';
  }
}

function marketCurrency(market: string): string {
  switch (market) {
    case 'HK':
      return 'HKD';
    case 'US':
      return 'USD';
    case 'CRYPTO':
      return 'USDT';
    default:
      return 'CNY';
  }
}

function sideLabel(side: string): string {
  switch (side) {
    case 'BUY':
      return '买入';
    case 'SELL':
      return '卖出';
    case 'DIVIDEND':
      return '股息';
    case 'DEPOSIT':
      return '入金';
    default:
      return '出金';
  }
}

function todayText(): string {
  const now = new Date();
  const m = now.getMonth() + 1;
  const d = now.getDate();
  return `${now.getFullYear()}-${m < 10 ? '0' : ''}${m}-${d < 10 ? '0' : ''}${d}`;
}

function toNumber(text: string): number {
  const value = parseFloat(text);
  return isNaN(value) ? 0 : value;
}

/** 手动记录：买入/卖出/股息/出入金，标的不存在时自动建。 */
@Component
export struct RecordPage {
  @State accounts: AccountItem[] = [];
  @State accountIndex: number = 0;
  @State side: string = 'BUY';
  @State market: string = 'A';
  @State symbol: string = '';
  @State assetName: string = '';
  @State quantity: string = '';
  @State price: string = '';
  @State fee: string = '';
  @State amountPerShare: string = '';
  @State tax: string = '';
  @State cash: string = '';
  @State tradedAt: string = todayText();
  @State note: string = '';
  @State submitting: boolean = false;
  @State resolved: string = '';

  async aboutToAppear(): Promise<void> {
    await this.loadAccounts();
  }

  async loadAccounts(): Promise<void> {
    try {
      const data = await ApiClient.get<ListResponse<AccountItem>>('/accounts/');
      this.accounts = data.results ? data.results : [];
    } catch (err) {
      console.warn(`load accounts failed: ${JSON.stringify(err)}`);
    }
  }

  needAsset(): boolean {
    return this.side === 'BUY' || this.side === 'SELL' || this.side === 'DIVIDEND';
  }

  /** 买入=-(量*价+费)，卖出=量*价-费，股息/入金=手填。 */
  previewText(): string {
    if (this.side === 'DEPOSIT' || this.side === 'WITHDRAW') {
      const value = toNumber(this.cash);
      const shown = this.side === 'WITHDRAW' ? -value : value;
      return `${shown.toFixed(2)} ${marketCurrency(this.market)}`;
    }
    if (this.side === 'DIVIDEND') {
      const gross = toNumber(this.amountPerShare) * toNumber(this.quantity);
      return `${(gross - toNumber(this.tax)).toFixed(2)} ${marketCurrency(this.market)}`;
    }
    const gross = toNumber(this.quantity) * toNumber(this.price);
    const cost = toNumber(this.fee);
    const value = this.side === 'BUY' ? -(gross + cost) : gross - cost;
    return `${value.toFixed(2)} ${marketCurrency(this.market)}`;
  }

  /** 标的按代码复用，不存在则创建（市场+代码唯一）。 */
  async resolveAsset(): Promise<number> {
    const symbol = this.symbol.trim();
    if (symbol.length === 0) {
      return 0;
    }
    const found = await ApiClient.get<ListResponse<AssetItem>>(`/assets/?search=${encodeURIComponent(symbol)}`);
    const list = found.results ? found.results : [];
    for (let i = 0; i < list.length; i++) {
      if (list[i].symbol === symbol) {
        this.resolved = `已匹配 ${list[i].symbol} ${list[i].name}`;
        return list[i].id;
      }
    }
    const created = await ApiClient.post<AssetItem>('/assets/', {
      symbol: symbol,
      name: this.assetName.trim(),
      market: this.market,
      currency: marketCurrency(this.market),
      is_dividend_asset: this.side === 'DIVIDEND'
    });
    this.resolved = `新建标的 ${created.symbol}`;
    return created.id;
  }

  async submit(): Promise<void> {
    if (this.submitting) {
      return;
    }
    if (this.accounts.length === 0) {
      promptAction.showToast({ message: '请先在后端创建账户' });
      return;
    }
    this.submitting = true;
    try {
      const accountId = this.accounts[this.accountIndex].id;
      const currency = marketCurrency(this.market);
      const date = `${this.tradedAt}T09:30:00`;
      if (this.side === 'DIVIDEND') {
        const assetId = await this.resolveAsset();
        if (assetId === 0) {
          promptAction.showToast({ message: '股息需填标的代码' });
          this.submitting = false;
          return;
        }
        const gross = toNumber(this.amountPerShare) * toNumber(this.quantity);
        const taxValue = toNumber(this.tax);
        await ApiClient.post('/transactions/dividends/', {
          asset: assetId,
          account: accountId,
          pay_date: this.tradedAt,
          amount_per_share: this.amountPerShare || null,
          shares: this.quantity || null,
          gross: gross.toString(),
          tax: taxValue.toString(),
          net: (gross - taxValue).toString(),
          currency: currency
        });
      } else if (this.side === 'DEPOSIT' || this.side === 'WITHDRAW') {
        const value = toNumber(this.cash);
        await ApiClient.post('/transactions/records/', {
          account: accountId,
          asset: null,
          side: this.side,
          amount: (this.side === 'WITHDRAW' ? -value : value).toString(),
          currency: currency,
          traded_at: date,
          note: this.note,
          client_request_id: `manual-${Date.now()}`
        });
      } else {
        const assetId = await this.resolveAsset();
        if (assetId === 0) {
          promptAction.showToast({ message: '买入/卖出需填标的代码' });
          this.submitting = false;
          return;
        }
        await ApiClient.post('/transactions/records/', {
          account: accountId,
          asset: assetId,
          side: this.side,
          quantity: this.quantity,
          price: this.price,
          fee: this.fee || '0',
          currency: currency,
          traded_at: date,
          note: this.note,
          client_request_id: `manual-${Date.now()}`
        });
      }
      promptAction.showToast({ message: '已记录' });
      this.quantity = '';
      this.price = '';
      this.fee = '';
      this.cash = '';
      this.amountPerShare = '';
      this.tax = '';
      this.note = '';
      this.resolved = '';
    } catch (err) {
      promptAction.showToast({ message: `失败：${JSON.stringify(err)}`.slice(0, 120) });
    } finally {
      this.submitting = false;
    }
  }

  @Builder
  field(label: string, value: string, placeholder: string, onChange: (text: string) => void) {
    Column({ space: 6 }) {
      Text(label).fontSize(13).fontColor('#8A8A8A')
      TextInput({ placeholder: placeholder, text: value })
        .backgroundColor('#FFFFFF')
        .borderRadius(10)
        .height(44)
        .padding({ left: 12, right: 12 })
        .onChange((text: string) => {
          onChange(text);
        })
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
    .margin({ bottom: 12 })
  }

  @Builder
  sideChip(text: string, active: boolean, onClick: () => void) {
    Text(text)
      .fontSize(14)
      .fontColor(active ? '#FFFFFF' : '#4A4A4A')
      .backgroundColor(active ? '#7166F0' : '#FFFFFF')
      .borderRadius(16)
      .padding({ left: 16, right: 16, top: 8, bottom: 8 })
      .margin({ right: 8 })
      .onClick(onClick)
  }

  build() {
    Scroll() {
      Column({ space: 4 }) {
        Text('记一笔')
          .fontSize(22)
          .fontWeight(FontWeight.Medium)
          .width('100%')
          .margin({ bottom: 12 })

        Row() {
          ForEach(SIDES, (item: string) => {
            this.sideChip(sideLabel(item), this.side === item, () => {
              this.side = item;
            })
          }, (item: string) => item)
        }
        .width('100%')
        .margin({ bottom: 16 })

        Column({ space: 6 }) {
          Text('账户').fontSize(13).fontColor('#8A8A8A')
          if (this.accounts.length > 0) {
            Select(this.accounts.map((item: AccountItem) => {
              const option: SelectOption = { value: `${item.name} · ${item.currency}` };
              return option;
            }))
              .selected(this.accountIndex)
              .value(this.accounts[this.accountIndex].name)
              .height(44)
              .fontSize(15)
              .backgroundColor('#FFFFFF')
              .borderRadius(10)
              .onSelect((index: number) => {
                this.accountIndex = index;
              })
          } else {
            Text('未加载账户，请检查后端连接').fontSize(14).fontColor('#E24B4A')
          }
        }
        .width('100%')
        .alignItems(HorizontalAlign.Start)
        .margin({ bottom: 12 })

        if (this.needAsset()) {
          Column({ space: 6 }) {
            Text('市场').fontSize(13).fontColor('#8A8A8A')
            Select(MARKETS.map((item: string) => {
              const option: SelectOption = { value: marketLabel(item) };
              return option;
            }))
              .selected(MARKETS.indexOf(this.market))
              .value(marketLabel(this.market))
              .height(44)
              .fontSize(15)
              .backgroundColor('#FFFFFF')
              .borderRadius(10)
              .onSelect((index: number) => {
                this.market = MARKETS[index];
              })
          }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
          .margin({ bottom: 12 })

          this.field('标的代码', this.symbol, '如 600519 / AAPL / BTC', (text: string) => {
            this.symbol = text;
          })
          this.field('标的名称（可留空）', this.assetName, '如 贵州茅台', (text: string) => {
            this.assetName = text;
          })
        }

        if (this.side === 'DIVIDEND') {
          this.field('持股数', this.quantity, '0', (text: string) => {
            this.quantity = text;
          })
          this.field('每股派息', this.amountPerShare, '0.00', (text: string) => {
            this.amountPerShare = text;
          })
          this.field('代扣税', this.tax, '0.00', (text: string) => {
            this.tax = text;
          })
        } else if (this.side === 'DEPOSIT' || this.side === 'WITHDRAW') {
          this.field('金额', this.cash, '0.00', (text: string) => {
            this.cash = text;
          })
        } else {
          this.field('数量', this.quantity, '0', (text: string) => {
            this.quantity = text;
          })
          this.field('成交价', this.price, '0.00', (text: string) => {
            this.price = text;
          })
          this.field('手续费', this.fee, '0.00', (text: string) => {
            this.fee = text;
          })
        }

        this.field('日期', this.tradedAt, 'YYYY-MM-DD', (text: string) => {
          this.tradedAt = text;
        })
        this.field('备注', this.note, '可留空', (text: string) => {
          this.note = text;
        })

        Row() {
          Text('现金变动').fontSize(14).fontColor('#8A8A8A')
          Text(this.previewText())
            .fontSize(18)
            .fontWeight(FontWeight.Medium)
            .fontColor('#7166F0')
        }
        .width('100%')
        .justifyContent(FlexAlign.SpaceBetween)
        .padding(14)
        .backgroundColor('#EFEDFD')
        .borderRadius(12)
        .margin({ bottom: 16 })

        if (this.resolved.length > 0) {
          Text(this.resolved).fontSize(12).fontColor('#8A8A8A').width('100%').margin({ bottom: 8 })
        }

        Button(this.submitting ? '提交中…' : '保存记录')
          .width('100%')
          .height(48)
          .backgroundColor('#7166F0')
          .fontSize(16)
          .enabled(!this.submitting)
          .onClick(() => {
            this.submit();
          })
      }
      .width('100%')
      .padding(16)
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F7F7F9')
  }
}
