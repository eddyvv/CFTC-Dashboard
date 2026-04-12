# CFTC最新及历史持仓数据获取

## 运行环境

* Windows 11
* Python 3.14.4
* Gemini 3.1Pro

## 持仓获取

获取持仓报告

```bash
python cftc_position_analysis.py              # 获取最新一期的持仓数据
python cftc_position_analysis.py --date <日期> # 获取指定日期的持仓数据 (格式: YYYY-MM-DD)
python cftc_position_analysis.py -h, --help   # 显示此帮助信息
```

报告生成位置`./0_持仓报告/cftc_持仓报告_YYYY-MM-DD.html`。

批量获取持仓报告

```bash
python cftc_batch_executor.py <起始时间> <结束时间>	#python batch_executor.py 2025-01-01 2026-04-10
```

报告生成位置`./0_持仓报告/cftc_持仓报告_YYYY-MM-DD.html。`

## 持仓分析

```bash
python cftc_generate_dashboard.py
```

持仓分析报告生成位置`./0_持仓报告/CFTC_交互式深度分析面板.html`。