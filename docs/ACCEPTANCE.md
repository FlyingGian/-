# 交付验收记录

记录日期：2026-10-20（北京时间）。版本1.0.0。执行环境Linux；不能把此记录当作Windows实机验收、真实投研策略验证或远程GitHub Actions成绩。

| 项目 | 结果 | 证据 |
|---|---|---|
| CPython3.12.14 全量自动测试 | 102通过 | tests-python312.xml |
| CPython3.13.16 全量自动测试 | 102通过 | tests-python313.xml |
| 领域/应用/基础设施总行覆盖率 | 87.50% | coverage.json / coverage_html |
| domain行覆盖率 | 92.85% | coverage.json，按domain文件加总 |
| 46因子防未来测试 | 逐因子通过 | test_no_lookahead_in_factor参数化测试 |
| 3证券逐日手算已知答案 | 通过，容差1e-6 | test_known_answer_three_stocks_daily_equity |
| 除权、送股、现金分红、停牌、涨跌停、T+1、最低佣金、现金不足 | 通过 | tests/test_domain.py |
| 财务公告次日、更正版本保留、历史成分和退市股票时点 | 通过构造测试 | tests/test_domain.py / test_data.py |
| 零成本与1/2/3倍成本、持仓数扰动、报告导出 | 通过 | test_service_selection_backtest_export |
| ML时间切分、purge/embargo、训练内预处理 | 通过 | test_ml_train_only_and_embargo |
| 新浪/腾讯解析、股手换算、八类异动、去抖、过期停发、退避恢复 | 通过模拟网络和夹具 | tests/test_realtime.py |
| 自选逐笔归档回放 | 合成场景匹配 | examples/SYNTHETIC_TEST_ONLY/replay_validation.json |
| 原生GUI启动和12页冒烟 | 通过Qt offscreen | tests/test_ui.py / desktop.png |
| Ruff / Black | 通过 | 仓库CI执行同样命令 |
| 依赖安装/检查 | 两个Linux独立环境兼容 | requirements.txt / constraints.txt |
| Windows3.12/3.13离线wheel解析 | 通过目标平台解析 | windows312-resolved.txt / windows313-resolved.txt；79个wheel，SHA256见wheels/manifest.json |
| 启动器四类环境分支 | Python逻辑模拟测试通过 | tests/test_bootstrap.py；不是cmd/注册表实机演示 |
| Windows干净机首次/二次启动、无Python、3.11、32位、PATH异常 | 待实机 | 已提供启动器、中文帮助与Windows CI |
| 真实全市场100轮、覆盖98%、P95≤1秒、内存/CPU/IO预算 | 未验证 | 当前两代码连接探测被代理403阻断，覆盖0；realtime_network_probe.json明确passed=false |
| 真实退市股/历史成分核对、两段历史压力、指数跟踪误差 | 未验证 | 未获得授权完整真实数据，不能用合成数据替代 |
| GitHub远程仓库、推送、Actions | 未执行 | 当前无可调用连接器，CLI认证失败；提供本地提交、bundle与workflow |

全部合成输出在SYNTHETIC_TEST_ONLY目录，页面、报告与Excel参数声明为测试，不作为今日选股建议。产品默认数据库为空。源码提供一键初始化入口，不包含未经授权的全市场数据。

复现自动测试（开发环境）：

```bash
python -m pytest -q --cov=domain --cov=application --cov=infrastructure
```

预期102通过；GUI无屏环境设置QT_QPA_PLATFORM=offscreen。Windows用户正常启动不需执行上述开发命令。第三方pyqtgraph在Qt销毁时可能打印一次信号断开RuntimeWarning，未导致测试失败；warning不等于网络或金融语义验证完成。

能力边界：日频撮合为保守近似，不是逐笔订单簿重建；盘中量比若无过去五日同时点数据则显示缺失；仅自选逐笔支持精确回放，全市场分钟聚合不能证明逐秒95%一致率；恢复连接记录需日频回补，由增量更新完成；历史财务修订、特殊限价、退市终止价值及税后分红仍依赖数据完整性。完整Brinson/Barra归因与尾盘历史分钟策略尚未集成进报告，不能声称已完成这些研究要求。软件可以用于可核验本地研究，尚不具备“全部P0与生产验收均完成”的证据。


## GitHub交付更新

目标仓库：https://github.com/FlyingGian/- 。源码与Release已成功发布，Git推送权限实际可用。v1.0.1修复CI的YAML解析问题，未更改金融算法；远程Windows/Linux测试的最终状态以仓库Actions为准。前面的“未推送”记录描述原始云端交付阶段。
