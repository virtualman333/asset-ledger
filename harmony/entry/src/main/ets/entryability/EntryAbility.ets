import { UIAbility, Want, common } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { BusinessError } from '@kit.BasicServicesKit';

export default class EntryAbility extends UIAbility {
  onCreate(): void {
    AppStorage.setOrCreate<string>('apiBaseUrl', 'http://10.0.2.2:8000/api/v1');
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
