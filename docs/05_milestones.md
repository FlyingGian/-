# 里程碑与验收

M1：CSV/在线适配器、SQLite/Parquet、质量闸门、真实日历；`python -m pytest tests/test_data.py -q`。

M2：至少40因子、截面处理、时点财务与成分、选股、撮合、风险；`python -m pytest tests/test_domain.py -q`。

M3：12页桌面、线程任务、K线/报告/导出、双源监控、归档回放；`QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui.py tests/test_realtime.py -q`（Windows 不设置此前缀）。

M4：启动器、全依赖锁、双版本 wheel、CI、文档、压缩包；`python -m pytest --cov=domain --cov=application --cov=infrastructure --cov-report=html --cov-report=term-missing`。

外部验收：真实源100轮测速、已退市股票历史抽查、两段真实压力区间、指数跟踪误差、Windows干净机四类异常和首次/二次启动计时。无数据或无对应机器时记录“待验证”，不能给通过章。正式研究需提供至少两年样本外，数据不足时报不满足而非缩短后仍宣称达标。
