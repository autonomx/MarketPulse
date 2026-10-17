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

; The install folder's length: NSIS cannot remove a file whose path is over 260 characters, and the deepest staged
; file is up to 128 characters below resources\runtime\ (MAX_RELATIVE_PATH in build/stage.py), so a longer folder
; would install but never update or uninstall cleanly. The directory page keeps Next disabled past 112 characters.
!ifndef BUILD_UNINSTALLER
  Function .onVerifyInstDir
    ; This file is included before LogicLib, so no ${if} outside the macros: up to 112 skips the Abort.
    StrLen $0 $INSTDIR
    IntCmp $0 112 +2 +2
      Abort
  FunctionEnd
!endif

; Before files are replaced or removed (install, update, uninstall): electron-builder's own check force-kills every
; process in the install folder after about a second, PostgreSQL included, and the uninstall then races their file
; handles. Instead ask MarketPulse to quit (`--quit`: it stops the backend and shuts PostgreSQL down cleanly), wait
; for every process started from the install folder to exit, and force them only after a minute.
;
; The install folder reaches PowerShell through an environment variable, never inside its command text, so a folder
; with an apostrophe (C:\Users\O'Brien\...) cannot break the check. Exit code 3 alone means "nothing running"; any
; other result, a PowerShell failure included, is treated as still running.
!macro customCheckAppRunning
  System::Call 'Kernel32::SetEnvironmentVariable(t "MARKETPULSE_INSTDIR", t "$INSTDIR\")i'
  StrCpy $R8 `"$PowerShellPath" -NoProfile -NonInteractive -C "if (@(Get-CimInstance -ClassName Win32_Process | ? {$$_.Path -and $$_.Path.StartsWith($$env:MARKETPULSE_INSTDIR, 'CurrentCultureIgnoreCase')}).Count -gt 0) { exit 0 } else { exit 3 }"`
  nsExec::Exec $R8
  Pop $R0
  ${if} $R0 != 3
    DetailPrint "$(appClosing)"
    ${if} ${FileExists} "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
      Exec `"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --quit`
    ${endIf}
    StrCpy $R1 0
    ${doWhile} $R0 != 3
      ${if} $R1 >= 60
        ${exitDo}
      ${endIf}
      Sleep 1000
      IntOp $R1 $R1 + 1
      nsExec::Exec $R8
      Pop $R0
    ${loop}
    ${if} $R0 != 3
      DetailPrint `"${PRODUCT_NAME}" did not quit within a minute; closing it.`
      nsExec::Exec `"$PowerShellPath" -NoProfile -NonInteractive -C "Get-CimInstance -ClassName Win32_Process | ? {$$_.Path -and $$_.Path.StartsWith($$env:MARKETPULSE_INSTDIR, 'CurrentCultureIgnoreCase')} | % { Stop-Process -Id $$_.ProcessId -Force }"`
      Sleep 2000
    ${endIf}
  ${endIf}
!macroend
