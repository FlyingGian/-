# 架构与扩展

presentation→application→domain；infrastructure实现外部I/O，application注入仓库和源。domain不导入Qt、数据库或HTTP。presentation/main.py仅协调控件与后台任务；后台线程通过信号回主线程，行情用1秒QTimer合并刷新，表格基于QAbstractTableModel。

Service管理个人YAML、数据覆盖、CSV导入、增量更新、选股与敏感度研究。LocalRepository把元数据写入SQLite，日线分年Parquet原子替换；业务数据内容计算快照哈希，audit不影响数据版本。批量导入按文件原子性，不宣称跨文件全事务；取消后保留已完成证券并继续增量。

ResearchData为无I/O内存快照，所有时点计算先裁剪日期。财务合并受公告日控制；成分为区间；states为每日快照。46个Factor元数据与compute共享注册表，方向与依赖可枚举。表达式只支持白名单AST算术，禁止eval/属性/导入。

回测独立维护现金与买入日lot；开盘先行动、再卖后买、收盘估值并生成未来目标。价格为raw，因子为hfq；T+1、上下限、参与率、交易单位、费率和回撤降仓单独实现。日频VWAP是事后执行代理，仅显式配置使用。配股未模型化时主动阻止。

实时适配器是批量HTTP，带令牌桶与有界并发。AlertEngine无I/O，滚动窗口与去抖；收盘日频与盘中实时目录分离。Collector以asyncio调度，归档通过asyncio.to_thread执行，停止时刷盘。自选逐笔与全市场分钟聚合有不同重放精度，不能混淆。恢复连接的日频缺口由增量更新补齐，绝不回写快照。

安全扩展：新增源继承BaseDataSource或RealtimeSource；新增因子需增加注册表定义、compute逻辑、as_of_date防未来测试及文档；新增交易规则应更新生效日期、来源、verified_through并重跑回测。新增ML标签必须明确持有区间，保留purge/embargo及独立样本外。

源码按GitHub组织，workflow、issue模板和本地bundle用于后续维护。当前缺远程认证，没有后台推送行为。
