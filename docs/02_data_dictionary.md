# 数据字典

所有日期 ISO YYYY-MM-DD；时间 ISO 8601 带 +08:00；代码六位字符串；金额元、数量股、比例为小数。来源、导入时间和哈希见 manifest。价格 raw；adj_factor 为向后累计复权因子。历史最早日期与缺失率从质量报告统计，不预填“2010 年齐全”。

| 表/文件 | 主键 | 字段与类型 | 更新/来源 |
|---|---|---|---|
| stocks | code | name:str, board:main/chinext/star/bse, list_date:date, delist_date:date可空 | 基础信息；CSV/Tushare/AkShare |
| calendar | date | is_open:bool | 真实交易日历；CSV/Tushare/AkShare |
| daily/year=YYYY/bars.parquet | code,date | open/high/low/close/prev_close:float元, volume:float股, amount:float元, adj_factor:float, float_shares:float股, market_cap:float元, suspended:bool, limit_up/down:float可空 | 收盘后，CSV/Tushare/AkShare；AkShare 不提供的字段保持缺失 |
| states | code,date | is_st:bool, industry:str, delisting:bool | 按日快照，CSV/Tushare；禁止最新状态回填 |
| financials | code,report_period,announce_date,version | announce_date:date可空, available_date:date可空, report_period:date, version:int, revenue/net_profit/equity/assets/operating_cf/gross_profit/ebitda/debt/cash/rd/goodwill:float元, revenue_yoy/profit_yoy/revenue_acceleration/institutional_ratio/pledge_ratio/holders_change:float小数 | 公告次一交易日；更正另存版本，不覆盖历史 |
| valuations | code,date | pe/pb/ps/pcf/ev_ebitda/dividend_yield:float | CSV/Tushare，收盘 |
| members | index_code,code,start_date | end_date:date可空，含首尾 | 历史成分区间；CSV/Tushare；当前名单不可代替 |
| actions | code,ex_date | cash_per_share:float税前元, share_ratio:float新增股/原股, rights_ratio:float, rights_price:float | 除权日现金与股数入账；配股缺乏授权则阻止回测 |
| audit | id | time,kind,detail | 更新、人工名单、配置变更记录 |
| realtime/YYYY-MM-DD/*.parquet | code,quote_ts,recv_ts | write_ts,price,prev_close,volume,amount,bid/ask五档 | 独立目录，异动全量，自选全量，其余分钟聚合 |

财务缺公告日：采用法定披露截止日作为保守日期，然后顺延到下一真实交易日；若截止日之后发生延迟披露而未知，仍可能有时点误差，必须在报告标记。年报截止次年 4/30；Q1 4/30；Q2 8/31；Q3 10/31。更正必须使用其自身公告日。不接受未注明口径的金额和成交量。

日频与实时物理隔离。所有导入先校验再原子替换；SQLite WAL，Parquet 临时文件后 rename；快照逐文件 SHA256，参数、规则、软件版本一起存入报告。日历、ST、成分或行动缺失不自动猜测。

补充：`benchmarks` 主键(code,date)，字段close:float总收益指数点位，供真实基准对比使用；不以策略股票截面均值冒充沪深300。若导入价格指数而非总收益指数，报告对比会受分红口径差异影响，应在输入来源声明中说明。

补充：Tushare报表适配将三张报表各自公告事件按当时已知字段组合，不用后发布的资产负债数据填早期利润表。所有已返回的版本本地保留，但无法恢复供应商未返回的历史修订。财务比率按报告期原始金额，非自动TTM。实际缺失率见数据管理，未实测的源字段不填写虚假百分比。
