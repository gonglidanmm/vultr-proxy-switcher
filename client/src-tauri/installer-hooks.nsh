; 安装前钩子：杀掉残留的 sing-box / proxy-client 进程，避免文件被占用（重装报「Error opening file for writing」）
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /IM sing-box.exe'
  nsExec::Exec 'taskkill /F /IM proxy-client.exe'
  Sleep 1000
!macroend
