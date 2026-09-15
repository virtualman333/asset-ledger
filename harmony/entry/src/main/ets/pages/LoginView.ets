import { ApiClient } from '../common/ApiClient';

/** 登录/注册。默认填好演示账号，方便首次联调。 */
@Component
export struct LoginView {
  @State username: string = 'demo';
  @State password: string = 'demo12345';
  @State error: string = '';
  @State isRegister: boolean = false;
  @State submitting: boolean = false;
  @State baseUrl: string = ApiClient.getBaseUrl();
  onSuccess: () => void = () => {
  };

  private async submit(): Promise<void> {
    this.error = '';
    this.submitting = true;
    try {
      AppStorage.setOrCreate<string>('apiBaseUrl', this.baseUrl);
      if (this.isRegister) {
        await ApiClient.register(this.username, this.password);
      }
      await ApiClient.login(this.username, this.password);
      this.onSuccess();
    } catch (err) {
      this.error = err instanceof Error ? err.message : JSON.stringify(err);
    } finally {
      this.submitting = false;
    }
  }

  build() {
    Column({ space: 16 }) {
      Text('资产账本')
        .fontSize(28)
        .fontWeight(FontWeight.Medium)
        .fontColor('#7166F0')

      TextInput({ placeholder: '后端地址，真机填局域网 IP', text: this.baseUrl })
        .onChange((value: string) => {
          this.baseUrl = value;
        })
        .fontSize(13)

      TextInput({ placeholder: '用户名', text: this.username })
        .onChange((value: string) => {
          this.username = value;
        })

      TextInput({ placeholder: '密码（≥8 位）', text: this.password })
        .type(InputType.Password)
        .onChange((value: string) => {
          this.password = value;
        })

      if (this.error.length > 0) {
        Text(this.error).fontSize(12).fontColor('#E24B4A').width('100%')
      }

      Button(this.isRegister ? '注册并登录' : '登录')
        .width('100%')
        .backgroundColor('#7166F0')
        .enabled(!this.submitting)
        .onClick(() => {
          this.submit();
        })

      Text(this.isRegister ? '已有账号，去登录' : '没有账号，去注册')
        .fontSize(13)
        .fontColor('#8A8A8A')
        .onClick(() => {
          this.isRegister = !this.isRegister;
        })
    }
    .width('100%')
    .height('100%')
    .padding(24)
    .justifyContent(FlexAlign.Center)
  }
}
