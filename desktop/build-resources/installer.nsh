; MarketPulse's uninstaller keeps the user's data (%LOCALAPPDATA%\MarketPulse: database, settings, logs) unless the
; user ticks "Remove my data" (roadmap MP-9.1).
!macro customUnInstall
  ${ifNot} ${isUpdated}
    MessageBox MB_YESNO|MB_ICONQUESTION "Also remove your MarketPulse data (database, settings, logs)?$\r$\n$\r$\nChoose No to keep it for a later install." /SD IDNO IDNO keepData
      RMDir /r "$LOCALAPPDATA\MarketPulse"
    keepData:
  ${endIf}
  ; The app registers itself to start at login; remove that entry with the app.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "com.autonomx.marketpulse"
!macroend
