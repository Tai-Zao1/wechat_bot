Unicode True
ManifestDPIAware True

!ifndef APP_NAME
  !define APP_NAME "PyWechat Bot GUI"
!endif

!ifndef APP_EXE
  !define APP_EXE "pywechat_bot_gui.exe"
!endif

!ifndef APP_DIR_NAME
  !define APP_DIR_NAME "PyWechatBotGui"
!endif

!ifndef APP_VERSION
  !define APP_VERSION "dev"
!endif

!ifndef DISPLAY_NAME
  !define DISPLAY_NAME "${APP_NAME} v${APP_VERSION}"
!endif

!ifndef OUT_NAME
  !define OUT_NAME "PyWechatBotInstaller_v${APP_VERSION}.exe"
!endif

!define INSTALL_DIR "$PROGRAMFILES64\${APP_DIR_NAME}"
!define SOURCE_DIR "..\dist\pywechat_bot_gui"
!define ASSETS_DIR "..\installer\assets"

!define SETUP_ICON "${ASSETS_DIR}\setup.ico"
!define UNINSTALL_ICON "${ASSETS_DIR}\uninstall.ico"
!define WELCOME_BITMAP "${ASSETS_DIR}\welcome.bmp"
!define HEADER_BITMAP "${ASSETS_DIR}\header.bmp"
!define UNINSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_DIR_NAME}"

!include "MUI2.nsh"

!macro !defineifexist _VAR_NAME _FILE_NAME
  !tempfile _TEMPFILE
  !ifdef NSIS_WIN32_MAKENSIS
    !system 'if exist "${_FILE_NAME}" echo !define ${_VAR_NAME} > "${_TEMPFILE}"'
  !else
    !system 'if [ -e "${_FILE_NAME}" ]; then echo "!define ${_VAR_NAME}" > "${_TEMPFILE}"; fi'
  !endif
  !include /NONFATAL "${_TEMPFILE}"
  !delfile /NONFATAL "${_TEMPFILE}"
  !undef _TEMPFILE
!macroend
!define !defineifexist "!insertmacro !defineifexist"

${!defineifexist} HAS_SETUP_ICON "${SETUP_ICON}"
${!defineifexist} HAS_UNINSTALL_ICON "${UNINSTALL_ICON}"
${!defineifexist} HAS_WELCOME_BITMAP "${WELCOME_BITMAP}"
${!defineifexist} HAS_HEADER_BITMAP "${HEADER_BITMAP}"

Name "${DISPLAY_NAME}"
Caption "${DISPLAY_NAME}"
OutFile "..\dist\${OUT_NAME}"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKLM "${UNINSTALL_REG_KEY}" "InstallLocation"
RequestExecutionLevel admin

!ifdef HAS_SETUP_ICON
  Icon "${SETUP_ICON}"
!endif

!ifdef HAS_UNINSTALL_ICON
  UninstallIcon "${UNINSTALL_ICON}"
!endif

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "${DISPLAY_NAME}"
!define MUI_FINISHPAGE_TITLE "${DISPLAY_NAME}"
!ifdef HAS_WELCOME_BITMAP
  !define MUI_WELCOMEFINISHPAGE_BITMAP "${WELCOME_BITMAP}"
!endif
!ifdef HAS_HEADER_BITMAP
  !define MUI_HEADERIMAGE
  !define MUI_HEADERIMAGE_BITMAP "${HEADER_BITMAP}"
!endif

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Function RunPreviousUninstaller
  IfFileExists "$INSTDIR\uninstall.exe" 0 done
  DetailPrint "检测到已安装版本，开始卸载旧版本"
  ClearErrors
  ExecWait '"$INSTDIR\uninstall.exe" /S _?=$INSTDIR' $0
  IfErrors 0 +3
    MessageBox MB_ICONSTOP|MB_OK "旧版本卸载启动失败。请先关闭正在运行的程序后重试。"
    Abort
  IntCmp $0 0 done_uninstall_check done_uninstall_failed done_uninstall_failed
done_uninstall_failed:
  MessageBox MB_ICONSTOP|MB_OK "旧版本卸载失败，退出码 $0。请先关闭正在运行的程序后重试。"
  Abort
done_uninstall_check:
  Sleep 500
done:
FunctionEnd

Section "Install"
  Call RunPreviousUninstaller
  SetOutPath "$INSTDIR"
  CreateDirectory "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  !ifdef HAS_SETUP_ICON
    File /oname=setup.ico "${SETUP_ICON}"
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\setup.ico"
  !else
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  !endif
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${UNINSTALL_REG_KEY}" "DisplayName" "${DISPLAY_NAME}"
  WriteRegStr HKLM "${UNINSTALL_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINSTALL_REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_REG_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKLM "${UNINSTALL_REG_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${UNINSTALL_REG_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINSTALL_REG_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_REG_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  DeleteRegKey HKLM "${UNINSTALL_REG_KEY}"
  DeleteRegKey HKCU "${UNINSTALL_REG_KEY}"
  RMDir /r "$INSTDIR"
SectionEnd
