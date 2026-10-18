/** The installed app's id: electron-builder's `appId`, its Windows AppUserModelID and start-at-login entry. */
export const APP_ID = 'com.autonomx.marketpulse';

/** The Start menu shortcut a Windows development run creates, so its notifications show. */
export const DEV_SHORTCUT_NAME = 'MarketPulse Dev.lnk';

/**
 * The AppUserModelID. Windows takes a window's taskbar icon from the Start menu shortcut with the same id, so a
 * development run has its own: sharing the installed app's would let the dev shortcut, which starts electron.exe,
 * give the installed app Electron's icon.
 */
export function appUserModelId(packaged: boolean): string {
  return packaged ? APP_ID : `${APP_ID}.dev`;
}
