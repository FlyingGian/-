# 红队验收报告

本报告区分代码证据、自动测试和外部实测。无法验证的项目不写“通过”。

| 维度 | 状态 | 证据 | 结论 |
|---|---|---|---|
|未来函数|通过构造测试|[domain/factors.py:136](../domain/factors.py#L136)；[domain/backtest.py:397](../domain/backtest.py#L397)|因子严格日期截断，信号最早次日执行。|
|幸存者偏差|逻辑通过，真实覆盖未验证|[domain/data.py:63](../domain/data.py#L63)|保留退市基础信息与历史成分区间；供应商完整性需要真实数据验收。|
|复权与分红|通过边界测试|[domain/data.py:98](../domain/data.py#L98)；[domain/backtest.py:163](../domain/backtest.py#L163)|raw价+显式现金股数，因子hfq；配股主动阻止，分红为税前。|
|涨跌停成交性|通过保守规则测试|[domain/backtest.py:242](../domain/backtest.py#L242)|不用全天后续是否开板倒推开盘成交。|
|T+1与交易单位|通过|[domain/backtest.py:248](../domain/backtest.py#L248)；[domain/backtest.py:59](../domain/backtest.py#L59)|卖出仅可卖lot；科创200起、北交100起、主板100倍数。|
|财务时点|通过逻辑与构造测试|[domain/data.py:33](../domain/data.py#L33)|公告后次一真实交易日；更正使用自身公告日，缺公告日保守截止日仍有延迟披露风险。|
|ST、退市与现行限价|部分通过|[domain/data.py:27](../domain/data.py#L27)；[domain/backtest.py:223](../domain/backtest.py#L223)|每日状态，实际证券限价优先；新规则未核验不能直接猜。退市终止价值未提供则不能声称完整收益。|
|成本真实性|实现通过，券商实际口径待验|[domain/backtest.py:91](../domain/backtest.py#L91)|真实历史税率表、最低佣金、滑点冲击、参与率；早期特殊费率和税后分红须核验。|
|统计有效性|统计工具通过，真实因子效力未知|[domain/analysis.py:9](../domain/analysis.py#L9)；[application/service.py:248](../application/service.py#L248)|IC/RankIC/分层/衰减/敏感度；两年切片不自动等于独立样本外。|
|机器学习泄漏|通过构造测试|[domain/analysis.py:111](../domain/analysis.py#L111)|按时间切分和标签期purge，预处理在训练样本fit。|
|数据质量|通过结构测试|[infrastructure/storage.py:90](../infrastructure/storage.py#L90)|不填零伪造财务；OHLC、主键、复权、单位、版本校验；真实数据对账待完成。|
|工程与界面|通过本环境测试|[presentation/widgets.py:146](../presentation/widgets.py#L146)|Qt线程任务、中文错误、取消、12页测试；Windows实机体验待验。|
|数据许可与收益声明|已实施|[application/reports.py:12](../application/reports.py#L12)|报告与UI显示声明；第三方许可保留，研究工具不下单。|
|实时批量与限流|通过模拟|[infrastructure/realtime.py:92](../infrastructure/realtime.py#L92)；[infrastructure/realtime.py:124](../infrastructure/realtime.py#L124)|500/批、6并发、限速、退避；真实源被代理阻断。|
|实时新鲜度|通过构造测试|[domain/realtime.py:113](../domain/realtime.py#L113)|quote_ts决定延迟，过期停发；保留快照并明确过期。|
|实时口径|通过单元一致性测试|[domain/metrics.py:7](../domain/metrics.py#L7)；[infrastructure/realtime.py:58](../infrastructure/realtime.py#L58)|共享涨跌幅/VWAP/换手等函数；无同时点历史则量比缺失，不用全日均量冒充。|
|环境兼容|双版本Linux通过，Windows解析通过，Windows实机待验|[bootstrap.py:15](../bootstrap.py#L15)|启动器ASCII、中文帮助独立文件、依赖hash含constraints、79wheel；未宣称5秒实测。|

已修复问题：实际前五大集中度约束、YAML日期序列化、空行情成功曲线、未解释复权变化、未核验新规则、现金不足、Windows条件依赖缺失、启动失败退出码，以及盘中开始监控时的UI磁盘读取。

仍需外部证据：完整退市历史与终止价值、供应方PIT修订历史、真实行情100轮和Windows干净机验证。报告未整合完整归因、全市场精确回放或自动尾盘分钟策略。这些不因单元测试全绿而自动完成。

结论：计算闭环可审查和复现；真实投资有效性与全部生产条件尚不能认定已通过。