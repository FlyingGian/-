# GitHub协作与上游依据

已读取公开上游许可证：akfamily/akshare（MIT）、waditu/tushare（BSD）、pyqtgraph/pyqtgraph（MIT），网络请求结果见github_sources.json。实现为本项目独立编写，没有把外部回测代码原样拼装为自己的成果。上游接口是设计参考，实测结果不从他人README直接抄为本项目成绩。

选择保守的可审计撮合，而非用通用框架默认成交模型；选择轻量HTTP日线适配而非强装AkShare完整依赖。AkShare 1.19.1依赖jsonpath>=0.82，当前包索引没有满足零编译目标的wheel，故AkShareSource保留为可选接口，默认不安装。Tushare SDK与pyqtgraph为实际依赖。

源码按GitHub仓库整理，含README、LICENSE、CHANGELOG、issue模板、CI矩阵（Windows/Linux × Python 3.12/3.13）与提交历史。此会话没有可用的GitHub连接器，CLI令牌认证失败，因此尚未创建远程仓库、推送提交或运行远程Actions。随包的git bundle可恢复本地历史。

维护者可将解压目录创建为私有仓库后推送。不要提交data、Token、.venv或个人报告。Token默认通过操作系统凭据库保存，配置快照不包含密钥。

```bash
git remote add origin <你的私有GitHub仓库地址>
git push -u origin main
```

以上是维护操作说明，不是桌面用户启动必需步骤。用户启动只需双击start.bat。


## 仓库交付更新

用户指定目标：https://github.com/FlyingGian/- 。已完成Git推送权限dry-run验证；原始README保存在ORIGINAL_REPOSITORY_README.md。新版本经普通fast-forward提交发布，不强推、不删除既有历史。Release工作流以受限GITHUB_TOKEN上传版本文件，不需要在聊天中传递密码或Token。远程构建是否通过须以Actions实际结果为准。前文“未推送”记录保留原交付时的状态。
