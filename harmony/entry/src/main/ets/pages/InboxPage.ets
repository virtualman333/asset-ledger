import { ApiClient, fmt } from '../common/ApiClient';
import { Draft } from '../common/Models';

/** Agent 收件箱：AI 只产出草稿，人工确认后才入账。 */
@Component
export struct InboxPage {
  @State drafts: Draft[] = [];
  @State loading: boolean = false;
  @State message: string = '';
  @State text: string = '';

  async aboutToAppear(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    try {
      const res = await ApiClient.get<{ results: Draft[] }>('/ingest/drafts/');
      this.drafts = res.results;
    } catch (err) {
      this.message = err instanceof Error ? err.message : JSON.stringify(err);
    } finally {
      this.loading = false;
    }
  }

  async submitText(): Promise<void> {
    if (this.text.trim().length === 0) {
      return;
    }
    try {
      await ApiClient.post('/ingest/text/', { text: this.text });
      this.text = '';
      this.message = '已提交识别';
      await this.load();
    } catch (err) {
      this.message = err instanceof Error ? err.message : JSON.stringify(err);
    }
  }

  async confirm(id: number): Promise<void> {
    try {
      await ApiClient.post(`/ingest/drafts/${id}/confirm/`, {});
      this.message = '已入账';
      await this.load();
    } catch (err) {
      this.message = err instanceof Error ? err.message : JSON.stringify(err);
    }
  }

  async discard(id: number): Promise<void> {
    try {
      await ApiClient.post(`/ingest/drafts/${id}/discard/`, {});
      await this.load();
    } catch (err) {
      this.message = err instanceof Error ? err.message : JSON.stringify(err);
    }
  }

  build() {
    Column({ space: 12 }) {
      Text('Agent 收件箱').fontSize(20).fontWeight(FontWeight.Medium).width('100%')

      Row({ space: 8 }) {
        TextInput({ placeholder: '粘贴券商成交文本，自动识别', text: this.text })
          .layoutWeight(1)
          .fontSize(13)
          .onChange((value: string) => {
            this.text = value;
          })
        Button('识别').fontSize(13).height(32).backgroundColor('#7166F0').onClick(() => {
          this.submitText();
        })
      }.width('100%')

      if (this.message.length > 0) {
        Text(this.message).fontSize(12).fontColor('#8A8A8A').width('100%')
      }

      if (this.drafts.length === 0) {
        Text('暂无待确认草稿').fontSize(13).fontColor('#8A8A8A').margin({ top: 40 })
      }

      List({ space: 10 }) {
        ForEach(this.drafts, (item: Draft) => {
          ListItem() {
            Column({ space: 8 }) {
              Row({ space: 8 }) {
                Text(item.result.side).fontSize(14).fontWeight(FontWeight.Medium)
                Text(item.result.symbol).fontSize(14)
                Blank()
                Text(`置信度 ${(item.confidence * 100).toFixed(0)}%`)
                  .fontSize(12)
                  .fontColor(item.confidence >= 0.8 ? '#1D9E75' : '#BA7517')
              }.width('100%')

              Text(`${fmt(item.result.quantity, 4)} × ${fmt(item.result.price)} ${item.result.currency}`)
                .fontSize(13)
                .fontColor('#8A8A8A')
                .width('100%')

              if (item.missing.length > 0) {
                Text(`待补全：${item.missing.join('、')}`)
                  .fontSize(12)
                  .fontColor('#E24B4A')
                  .width('100%')
              }

              Row({ space: 12 }) {
                Button('确认入账')
                  .fontSize(13)
                  .height(30)
                  .backgroundColor('#7166F0')
                  .onClick(() => {
                    this.confirm(item.id);
                  })
                Button('丢弃')
                  .fontSize(13)
                  .height(30)
                  .backgroundColor('#B4B2A9')
                  .onClick(() => {
                    this.discard(item.id);
                  })
              }.width('100%')
            }
            .width('100%')
            .padding(14)
            .borderRadius(12)
            .backgroundColor('#FFFFFF')
          }
        }, (item: Draft) => `${item.id}`)
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
