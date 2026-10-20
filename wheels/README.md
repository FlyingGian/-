# Windows离线依赖

配套离线压缩包含79个预编译wheel与SHA256 manifest，覆盖CPython3.12/3.13 x64。将完整wheels目录与start.bat放在同一源码根目录。双击启动器自动优先从此目录安装。默认不要求使用者编译任何扩展。

源码轻量包仅保留本说明、manifest与生成脚本；完整wheel见单独离线依赖包。不要只下载某几个wheel，间接依赖也必须完整。Windows条件依赖已通过Windows目标环境离线解析校验。
