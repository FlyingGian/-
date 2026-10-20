# 因子清单（由注册表同步生成）

统一口径：每个因子只接收 `as_of_date` 及之前数据；价格收益为 close × adj_factor；财务按公告后下一真实交易日；缺失保持缺失。方向 +1 越大越好，-1 越小越好。Beta/IVOL 使用同期股票截面等权市场代理，非指数基准。

| ID | 名称 | 类别 | 方向 | 最少观察日 | 公式 | 依赖 |
|---|---|---|---|---|---|---|
|ep|盈利收益率|价值|1|1|`1/pe`|估值|
|bp|账面市值比|价值|1|1|`1/pb`|估值|
|sp|销售收益率|价值|1|1|`1/ps`|估值|
|cfp|现金流收益率|价值|1|1|`1/pcf`|估值|
|dividend_yield|股息率|价值|1|1|`dividend_yield`|估值|
|ev_ebitda|企业价值倍数|价值|-1|1|`ev_ebitda`|估值|
|roe|净资产收益率|质量|1|1|`net_profit/equity`|公告财务|
|roa|资产收益率|质量|1|1|`net_profit/assets`|公告财务|
|gross_margin|毛利率|质量|1|1|`gross_profit/revenue`|公告财务|
|net_margin|净利率|质量|1|1|`net_profit/revenue`|公告财务|
|asset_turnover|资产周转率|质量|1|1|`revenue/assets`|公告财务|
|accruals|应计利润|质量|-1|1|`(net_profit-operating_cf)/assets`|公告财务|
|cash_quality|现金利润比|质量|1|1|`operating_cf/net_profit`|公告财务|
|leverage|财务杠杆|质量|-1|1|`assets/equity`|公告财务|
|debt_ratio|负债率|质量|-1|1|`debt/assets`|公告财务|
|revenue_growth|营收同比|成长|1|1|`revenue_yoy`|公告财务|
|profit_growth|利润同比|成长|1|1|`profit_yoy`|公告财务|
|growth_acceleration|营收增速改善|成长|1|1|`revenue_acceleration`|公告财务|
|rd_ratio|研发费用率|成长|1|1|`rd/revenue`|公告财务|
|momentum_20|20日动量|动量反转|1|21|`hfq[-1]/hfq[-21]-1`|日线复权|
|momentum_60|60日动量|动量反转|1|61|`hfq[-1]/hfq[-61]-1`|日线复权|
|momentum_120|120日动量|动量反转|1|121|`hfq[-1]/hfq[-121]-1`|日线复权|
|reversal_5|5日反转|动量反转|-1|6|`hfq[-1]/hfq[-6]-1`|日线复权|
|reversal_1|1日反转|动量反转|-1|2|`hfq[-1]/hfq[-2]-1`|日线复权|
|momentum_skip5|剔除近5日动量|动量反转|1|61|`hfq[-6]/hfq[-61]-1`|日线复权|
|volatility_20|20日波动|波动风险|-1|21|`std(ret[-20:])*sqrt(252)`|日线复权|
|volatility_60|60日波动|波动风险|-1|61|`std(ret[-60:])*sqrt(252)`|日线复权|
|downside_vol|下行波动|波动风险|-1|21|`sqrt(mean(min(ret,0)^2))*sqrt(252)`|日线复权|
|max_drawdown_60|60日回撤|波动风险|-1|60|`max(1-hfq/cummax(hfq))`|日线复权|
|range_20|20日振幅|波动风险|-1|20|`mean((high-low)/prev_close)`|日线|
|beta_60|60日市场Beta|波动风险|-1|61|`cov(ret,market_ret)/var(market_ret)`|同期截面市场代理|
|ivol_60|60日特质波动|波动风险|-1|61|`std(ret-beta*market_ret)*sqrt(252)`|同期截面市场代理|
|turnover_20|20日换手率|流动情绪|-1|20|`mean(volume/float_shares)`|日线流通股本|
|abnormal_turnover|异常换手|流动情绪|-1|21|`turnover[-1]/mean(turnover[-21:-1])`|日线流通股本|
|amount_20|20日成交额|流动情绪|1|20|`mean(amount[-20:])`|日线|
|volume_ratio_5|日频5日量比|流动情绪|-1|6|`volume[-1]/mean(volume[-6:-1])`|日线；不冒充盘中同时点量比|
|amihud_20|Amihud非流动性|流动情绪|-1|21|`mean(abs(ret)/amount)`|日线|
|turnover_change|换手率变化|流动情绪|-1|40|`mean(turn[-20:])/mean(turn[-40:-20])-1`|日线流通股本|
|amount_growth|成交额变化|流动情绪|1|40|`mean(amount[-20:])/mean(amount[-40:-20])-1`|日线|
|log_size|对数市值|规模其他|-1|1|`log(market_cap)`|日线市值|
|holders_change|股东户数变化|规模其他|-1|1|`holders_change`|公告财务|
|institutional_ratio|机构持股比例|规模其他|1|1|`institutional_ratio`|公告财务|
|pledge_ratio|股权质押比例|规模其他|-1|1|`pledge_ratio`|公告财务|
|goodwill_ratio|商誉净资产比|规模其他|-1|1|`goodwill/equity`|公告财务|
|cash_ratio|现金资产比|质量|1|1|`cash/assets`|公告财务|
|price_ma20|价格均线偏离|动量反转|-1|20|`hfq[-1]/mean(hfq[-20:])-1`|日线复权|

适用与失效：估值在成长溢价和亏损股上可能失效；质量存在行业口径差异；成长受基数与重述影响；短期反转/换手受手续费与拥挤影响；小市值在2017–2020等区间曾显著衰减，2024年初存在踩踏风险；动量与低波可能在快速反弹期反向。这里是风险假设，不把未实测区间宣称为统计结论。

预期换手率：由具体股票池、调仓频率、过滤条件实测，不为单个因子编造固定数字。应先做IC、分组、衰减、交易成本与分风格样本外验证，再决定权重。