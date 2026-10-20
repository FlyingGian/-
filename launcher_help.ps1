Add-Type -AssemblyName PresentationFramework
$message = @"
需要 CPython 3.12 或 3.13（64 位）。

1. 尚未安装：下载 Windows installer (64-bit)。
2. 当前是 3.11 或更低：安装受支持版本。
3. 当前是 32 位：改装 64 位。
4. 已安装但找不到：修复安装，勾选 Add python.exe to PATH 和 Python Launcher。
5. 当前是 3.14：本软件暂只支持 3.12 / 3.13。

官网下载：https://www.python.org/downloads/windows/
"@
[System.Windows.MessageBox]::Show($message, 'AshareQuant 启动帮助', 'OK', 'Warning') | Out-Null
