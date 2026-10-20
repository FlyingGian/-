# 交付包使用

`AshareQuant-v1.0.0-source.zip`为完整源码、启动器、文档、测试证据、报告样例、界面截图、wheel清单和Git bundle。

`AshareQuant-Windows-Wheels.zip`为79个Windows预编译依赖（约465MiB），覆盖CPython3.12/3.13 x64。两个压缩包解压到同一父目录，将合并为一个AshareQuant文件夹。安装Python后双击该文件夹中的start.bat即可。

`SHA256SUMS.txt`校验两个包和bundle；源码包内SOURCE_MANIFEST.json逐文件记录SHA256及Git提交。离线依赖包内wheels/manifest.json记录每个wheel的SHA256。

默认运行数据库为空，防止把测试数据当真实。先阅读README完成数据准备。examples/SYNTHETIC_TEST_ONLY为明确合成输入和报告，供软件验收，不是今日荐股或真实业绩。

开发维护者可使用bundle恢复提交：`git clone AshareQuant.git.bundle recovered`。源码目录本身不要求用户会Git。当前未推送远程GitHub，原因与后续操作见GITHUB.md。
