import { UIAbility, Want, common } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { BusinessError } from '@kit.BasicServicesKit';
import { DEFAULT_API_BASE_URL } from '../common/ApiClient';

export default class EntryAbility extends UIAbility {
  onCreate(): void {
    // 默认地址的**唯一定义**在 `common/ApiClient.ets`；这里只是把它预置进 AppStorage
    AppStorage.setOrCreate<string>('apiBaseUrl', DEFAULT_API_BASE_URL);
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    AppStorage.setOrCreate<common.UIAbilityContext>('abilityContext', this.context);
    windowStage.loadContent('pages/Index', (err: BusinessError) => {
      if (err.code) {
        console.error(`loadContent failed: ${JSON.stringify(err)}`);
        return;
      }
      const win = windowStage.getMainWindowSync();
      win.setWindowLayoutFullScreen(false);
    });
  }
}
