import os
import glob
import re
import json
import sys
import time
import pandas as pd
import akshare as ak
import yfinance as yf
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings('ignore')

# ================= 配置区 =================
HTML_PATTERN = "cftc_持仓报告_*.html"
OUTPUT_FILE = "CFTC_交互式深度分析面板.html"

# 针对不同资产配置最优的数据源映射策略
ASSET_CONFIG = {
    # 宏观利率 (真实收益率 %)
    '2年期美债': {'type': 'us_yield', 'column': '美国国债收益率2年'},
    '10年期美债': {'type': 'us_yield', 'column': '美国国债收益率10年'},
    '超长期美债': {'type': 'us_yield', 'column': '美国国债收益率30年'},
    
    # 外盘商品期货 (原味期货美元报价)
    '黄金': {'type': 'futures', 'symbol': 'GC'},
    '白银': {'type': 'futures', 'symbol': 'SI'},
    '铜': {'type': 'futures', 'symbol': 'HG'},
    'WTI原油': {'type': 'futures', 'symbol': 'CL'},
    '天然气': {'type': 'futures', 'symbol': 'NG'},
    '玉米': {'type': 'futures', 'symbol': 'C'},
    
    # 核心股指 (直接拉取原生指数点数，带 ETF 防断连降级保护)
    '标普500': {'type': 'index_sina', 'symbol': '.INX', 'fallback_etf': 'SPY', 'desc': '标普500原生指数'},
    '纳斯达克100': {'type': 'index_sina', 'symbol': '.NDX', 'fallback_etf': 'QQQ', 'desc': '纳斯达克100原生指数'},
    '日经225': {'type': 'index_investing', 'country': '日本', 'index_name': '日经225', 'fallback_etf': 'EWJ', 'desc': '日经225原生指数'},
    
    # 外汇原生汇率指数 (通过 YFinance 获取真实汇率, 带 ETF 降级)
    '欧元/美元': {'type': 'yf_asset', 'symbol': 'EURUSD=X', 'fallback_etf': 'FXE', 'desc': 'EUR/USD 原生汇率指数'},
    '英镑/美元': {'type': 'yf_asset', 'symbol': 'GBPUSD=X', 'fallback_etf': 'FXB', 'desc': 'GBP/USD 原生汇率指数'},
    '日元/美元': {'type': 'yf_asset', 'symbol': 'JPYUSD=X', 'fallback_etf': 'FXY', 'desc': 'JPY/USD 原生汇率指数'},
    '澳元/美元': {'type': 'yf_asset', 'symbol': 'AUDUSD=X', 'fallback_etf': 'FXA', 'desc': 'AUD/USD 原生汇率指数'},
    
    # 比特币 CME 官方期货
    '比特币': {'type': 'yf_asset', 'symbol': 'BTC=F', 'fallback_etf': 'BITO', 'desc': 'CME 比特币期货 (BTC=F)'},

    # 其他外汇与宽基 (使用高流动性 ETF 代理，确保数据100%稳定)
    '罗素2000': {'type': 'etf_proxy', 'symbol': 'IWM', 'desc': 'IWM ETF 代理'},
    'MSCI新兴市场': {'type': 'etf_proxy', 'symbol': 'EEM', 'desc': 'EEM ETF 代理'},
    'MSCI发达市场': {'type': 'etf_proxy', 'symbol': 'EFA', 'desc': 'EFA ETF 代理'},
    '联邦基金': {'type': 'etf_proxy', 'symbol': 'BIL', 'desc': 'BIL 短债基准代理'}
}
# ==========================================

def clean_number(text):
    if not text: return 0
    cleaned = re.sub(r'[^\d\-]', '', text)
    try:
        return int(cleaned) if cleaned and cleaned != '-' else 0
    except ValueError:
        return 0

def parse_html_file(filepath):
    match = re.search(r'\d{4}-\d{2}-\d{2}', filepath)
    if not match: return []
    date_str = match.group()
    
    data_list = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            for row in soup.find_all('tr'):
                cols = row.find_all('td')
                if len(cols) >= 11 and not cols[0].has_attr('colspan'):
                    asset_name = cols[0].text.strip()
                    if asset_name:
                        data_list.append({
                            'Date': date_str,
                            'Asset': asset_name,
                            'Net': clean_number(cols[2].text),
                            'Long': clean_number(cols[5].text),
                            'Short': clean_number(cols[8].text)
                        })
    except Exception as e:
        pass
    return data_list

def enrich_with_prices(df):
    """根据资产类别使用最优数据源精准拉取历史数据"""
    print("\n🌐 开始自动分配最佳数据源拉取资产走势 (支持无缝降级保护)...")
    df['Price'] = None
    assets = df['Asset'].unique()
    
    min_date = df['Date'].min() - pd.Timedelta(days=7)
    max_date = df['Date'].max() + pd.Timedelta(days=7)
    
    # 1. 预先拉取【美债收益率】数据池
    yield_data_cache = None
    try:
        print("📊 正在初始化【宏观利率】数据池...")
        yield_data_cache = ak.bond_zh_us_rate(start_date="20200101")
        yield_data_cache['日期'] = pd.to_datetime(yield_data_cache['日期'])
        yield_data_cache.set_index('日期', inplace=True)
    except Exception as e:
        print(f"⚠️ 宏观利率初始化失败: {e}")

    # 2. 逐一分类拉取其余资产
    for asset in assets:
        if asset not in ASSET_CONFIG:
            continue
            
        cfg = ASSET_CONFIG[asset]
        desc = cfg.get('desc', '期货报价') if cfg['type'] != 'us_yield' else '官方收益率'
        sys.stdout.write(f"\r📈 正在拉取: {asset} ({desc}) ...       ")
        sys.stdout.flush()
        
        try:
            close_px = pd.Series(dtype=float)
            
            # --- 策略 A：国债收益率 ---
            if cfg['type'] == 'us_yield' and yield_data_cache is not None:
                col = cfg['column']
                if col in yield_data_cache.columns:
                    close_px = yield_data_cache[col].dropna()
                    
            # --- 策略 B：外盘商品期货 ---
            elif cfg['type'] == 'futures':
                hist = ak.futures_foreign_hist(symbol=cfg['symbol'])
                if not hist.empty:
                    hist.columns = [str(c).lower() for c in hist.columns]
                    if 'date' in hist.columns and 'close' in hist.columns:
                        hist['date'] = pd.to_datetime(hist['date'])
                        hist.set_index('date', inplace=True)
                        close_px = hist['close'].astype(float).dropna()

            # --- 策略 C：美股核心指数 (Sina 接口) ---
            elif cfg['type'] == 'index_sina':
                hist = ak.index_us_stock_sina(symbol=cfg['symbol'])
                if not hist.empty:
                    hist.columns = [str(c).lower() for c in hist.columns]
                    if 'date' in hist.columns and 'close' in hist.columns:
                        hist['date'] = pd.to_datetime(hist['date'])
                        hist.set_index('date', inplace=True)
                        close_px = hist['close'].astype(float).dropna()

            # --- 策略 D：全球核心指数 (Investing 接口) ---
            elif cfg['type'] == 'index_investing':
                hist = ak.index_investing_global(
                    country=cfg['country'], 
                    index_name=cfg['index_name'], 
                    period="每日", 
                    start_date="20200101", 
                    end_date="20300101"
                )
                if not hist.empty and '收盘' in hist.columns and '日期' in hist.columns:
                    hist['date'] = pd.to_datetime(hist['日期'])
                    hist.set_index('date', inplace=True)
                    close_px = hist['收盘'].astype(str).str.replace(',', '').astype(float).dropna()
                    
            # --- 策略 E：混合 Yahoo Finance (针对外汇指数与加密期货) ---
            elif cfg['type'] == 'yf_asset':
                try:
                    ticker_obj = yf.Ticker(cfg['symbol'])
                    hist = ticker_obj.history(start=min_date.strftime('%Y-%m-%d'), end=max_date.strftime('%Y-%m-%d'))
                    if not hist.empty and 'Close' in hist.columns:
                        if hist.index.tz is not None:
                            hist.index = hist.index.tz_localize(None)
                        close_px = hist['Close'].astype(float).dropna()
                except Exception:
                    pass # 失败则留空，交由下方的防断连降级保护处理
            
            # --- 策略 F：ETF 代理 ---
            elif cfg['type'] == 'etf_proxy':
                hist = ak.stock_us_daily(symbol=cfg['symbol'], adjust="qfq")
                if not hist.empty:
                    hist.columns = [str(c).lower() for c in hist.columns]
                    if 'date' in hist.columns and 'close' in hist.columns:
                        hist['date'] = pd.to_datetime(hist['date'])
                        hist.set_index('date', inplace=True)
                        close_px = hist['close'].astype(float).dropna()

            # --- 防断连降级保护 (如果原生指数/外汇获取失败，自动用 ETF 补位) ---
            if close_px.empty and 'fallback_etf' in cfg:
                sys.stdout.write(f" [降级使用 ETF: {cfg['fallback_etf']}] ")
                sys.stdout.flush()
                hist = ak.stock_us_daily(symbol=cfg['fallback_etf'], adjust="qfq")
                if not hist.empty:
                    hist.columns = [str(c).lower() for c in hist.columns]
                    if 'date' in hist.columns and 'close' in hist.columns:
                        hist['date'] = pd.to_datetime(hist['date'])
                        hist.set_index('date', inplace=True)
                        close_px = hist['close'].astype(float).dropna()

            # --- 数据对齐到 CFTC 的报告日 (周二) ---
            if not close_px.empty:
                if close_px.index.tz is not None:
                    close_px.index = close_px.index.tz_localize(None)
                    
                mask = df['Asset'] == asset
                asset_dates = df.loc[mask, 'Date']
                
                prices = []
                for d in asset_dates:
                    available_dates = close_px[close_px.index <= d]
                    if not available_dates.empty:
                        prices.append(float(available_dates.iloc[-1]))
                    else:
                        prices.append(None)
                        
                df.loc[mask, 'Price'] = prices
                
            # 温和休眠，保护 IP
            time.sleep(1.0)
            
        except Exception as e:
            print(f"\n⚠️ {asset} 获取遭遇异常: {e}")
            
    print("\n✅ 所有资产网络数据匹配完成！")
    return df

def generate_dashboard(df):
    df = df.sort_values(['Asset', 'Date'])
    assets = sorted(df['Asset'].unique().tolist())
    
    full_data = {}
    for asset in assets:
        if asset not in ASSET_CONFIG:
            continue
            
        sub = df[df['Asset'] == asset]
        safe_prices = [float(p) if pd.notna(p) else None for p in sub['Price']]
        
        full_data[asset] = {
            'dates': sub['Date'].dt.strftime('%Y-%m-%d').tolist(),
            'longs': sub['Long'].tolist(),
            'shorts': sub['Short'].tolist(),
            'nets': sub['Net'].tolist(),
            'prices': safe_prices,
            'config': ASSET_CONFIG[asset]
        }

    latest_date = df['Date'].max().strftime('%Y-%m-%d')
    
    html_template = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>CFTC 全维度智能量价面板</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        body {{ display: flex; height: 100vh; margin: 0; font-family: sans-serif; background: #f0f2f5; }}
        #sidebar {{ width: 280px; background: #fff; border-right: 1px solid #ddd; display: flex; flex-direction: column; }}
        .search-box {{ padding: 15px; border-bottom: 1px solid #eee; background: #fafafa; }}
        #assetSearch {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; outline: none; font-size: 14px; transition: border-color 0.2s; }}
        #assetSearch:focus {{ border-color: #1890ff; }}
        #assetList {{ flex: 1; overflow-y: auto; padding: 10px; }}
        .asset-btn {{ width: 100%; text-align: left; padding: 12px 15px; margin-bottom: 6px; border: none; background: transparent; cursor: pointer; border-radius: 6px; font-size: 14px; font-weight: 500; transition: all 0.2s; border-left: 4px solid transparent;}}
        .asset-btn:hover {{ background: #e6f7ff; color: #1890ff; }}
        .asset-btn.active {{ background: #e6f7ff; color: #1890ff; border-left: 4px solid #1890ff; font-weight: bold; }}
        #main {{ flex: 1; display: flex; flex-direction: column; padding: 20px; overflow: hidden; }}
        header {{ margin-bottom: 20px; background: #fff; padding: 20px 25px; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        h1 {{ margin: 0 0 8px 0; font-size: 22px; color: #1a1a1a; display: flex; align-items: center; gap: 10px; }}
        .badge-type {{ font-size: 12px; padding: 4px 8px; border-radius: 4px; font-weight: normal; }}
        .type-yield {{ background: #fff1f0; color: #f5222d; border: 1px solid #ffa39e; }}
        .type-futures {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
        .type-proxy {{ background: #e6f7ff; color: #1890ff; border: 1px solid #91d5ff; }}
        .type-index {{ background: #f9f0ff; color: #722ed1; border: 1px solid #d3adf7; }}
        .type-yf {{ background: #fffbe6; color: #fa8c16; border: 1px solid #ffe58f; }}
        .info {{ color: #666; font-size: 14px; display: flex; flex-direction: column; gap: 5px; }}
        .sub-info {{ font-size: 13px; color: #888; }}
        #chart-container {{ flex: 1; background: #fff; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); padding: 20px; min-height: 400px; }}
    </style>
</head>
<body>
    <div id="sidebar">
        <div class="search-box">
            <input type="text" id="assetSearch" placeholder="🔍 搜索资产 (如: 美债, 外汇)">
        </div>
        <div id="assetList"></div>
    </div>
    <div id="main">
        <header>
            <h1>
                <span id="currentAsset">请选择资产</span>
                <span id="typeBadge" class="badge-type"></span>
            </h1>
            <div class="info">
                <span>统计区间: {df['Date'].min().strftime('%Y-%m-%d')} 至 {latest_date}</span>
                <span class="sub-info" id="dataSourceDesc"></span>
            </div>
        </header>
        <div id="chart-container">
            <div id="chart" style="width: 100%; height: 100%;"></div>
        </div>
    </div>

    <script>
        const rawData = {json.dumps(full_data)};
        const assetList = Object.keys(rawData);
        let myChart = echarts.init(document.getElementById('chart'));

        function renderAssetList(filter = '') {{
            const container = document.getElementById('assetList');
            container.innerHTML = '';
            assetList.filter(a => a.toLowerCase().includes(filter.toLowerCase())).forEach(asset => {{
                const btn = document.createElement('button');
                btn.className = 'asset-btn';
                btn.innerText = asset;
                btn.onclick = () => selectAsset(asset, btn);
                container.appendChild(btn);
            }});
        }}

        function selectAsset(name, btnElement) {{
            document.querySelectorAll('.asset-btn').forEach(b => b.classList.remove('active'));
            if(btnElement) btnElement.classList.add('active');
            
            document.getElementById('currentAsset').innerText = name + " - 量价对冲分析";
            
            const data = rawData[name];
            const cfg = data.config;
            
            const badge = document.getElementById('typeBadge');
            const desc = document.getElementById('dataSourceDesc');
            let priceAxisName = '资产价格';
            let tooltipUnit = '';
            
            if (cfg.type === 'us_yield') {{
                badge.className = 'badge-type type-yield';
                badge.innerText = '宏观收益率曲线';
                desc.innerHTML = '⚠️ <strong>债市法则：</strong>国债期货持仓(多头做多价格) 与 收益率(紫线) 呈 <strong>反向关系</strong>。';
                priceAxisName = '收益率 (%)';
                tooltipUnit = '%';
            }} else if (cfg.type === 'futures') {{
                badge.className = 'badge-type type-futures';
                badge.innerText = '外盘商品期货';
                desc.innerHTML = '💡 <strong>数据说明：</strong>紫线为海外官方主力连续合约美元报价。';
                priceAxisName = '期货报价 ($)';
            }} else if (cfg.type === 'index_sina' || cfg.type === 'index_investing') {{
                badge.className = 'badge-type type-index';
                badge.innerText = '原生指数走势';
                desc.innerHTML = '💡 <strong>数据说明：</strong>直接获取官方核心指数的绝对点数。';
                priceAxisName = '指数点数';
            }} else if (cfg.type === 'yf_asset') {{
                badge.className = 'badge-type type-yf';
                badge.innerText = name.includes('比特币') ? '原生期货合约' : '原生汇率指数';
                desc.innerHTML = '💡 <strong>数据说明：</strong>精准对接 <strong>' + cfg.symbol + '</strong> 原生行情走势。';
                priceAxisName = name.includes('比特币') ? '期货报价 ($)' : '汇率指数';
            }} else if (cfg.type === 'etf_proxy') {{
                badge.className = 'badge-type type-proxy';
                badge.innerText = '指数 ETF 穿透代理';
                desc.innerHTML = '💡 <strong>数据说明：</strong>使用高流动性 ETF (<strong>' + cfg.symbol + '</strong>) 代理真实走势。';
                priceAxisName = 'ETF 价格 ($)';
            }}

            const hasPrice = data.prices.some(p => p !== null);
            
            const option = {{
                tooltip: {{ 
                    trigger: 'axis', 
                    axisPointer: {{ type: 'cross', crossStyle: {{ color: '#999' }} }},
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    borderColor: '#ccc',
                    borderWidth: 1,
                    textStyle: {{ color: '#333' }},
                    formatter: function (params) {{
                        let html = '<div style="font-weight:bold;margin-bottom:8px;border-bottom:1px solid #eee;padding-bottom:5px;">' + params[0].name + '</div>';
                        params.forEach(param => {{
                            let val = param.value;
                            if (param.seriesIndex === 3 && val != null) {{
                                // 智能动态小数位：汇率需要4位小数(如0.0065)，收益率3位，其余2位
                                let decimals = 2;
                                if (cfg.type === 'us_yield') decimals = 3;
                                else if (name.includes('/') || name.includes('汇率')) decimals = 4; 
                                
                                val = Number(val).toLocaleString(undefined, {{
                                    minimumFractionDigits: decimals, 
                                    maximumFractionDigits: decimals
                                }}) + tooltipUnit;
                            }} else if (val != null) {{
                                val = Number(val).toLocaleString() + ' 手';
                            }}
                            
                            html += '<div style="display:flex;justify-content:space-between;min-width:240px;margin:4px 0;">' +
                                    '<span>' + param.marker + param.seriesName + '</span>' + 
                                    '<span style="font-weight:bold; margin-left:15px;">' + (val == null ? '未获取' : val) + '</span>' +
                                    '</div>';
                        }});
                        return html;
                    }}
                }},
                legend: {{ data: ['多头 (Long)', '空头 (Short)', '净持仓 (Net)', priceAxisName], top: 5 }},
                grid: {{ left: '4%', right: '5%', bottom: '10%', top: '15%', containLabel: true }},
                dataZoom: [
                    {{ type: 'slider', start: 0, end: 100, bottom: 0, height: 25 }}, 
                    {{ type: 'inside' }}
                ],
                xAxis: {{ type: 'category', data: data.dates, boundaryGap: true, axisTick: {{ alignWithLabel: true }} }},
                yAxis: [
                    {{ 
                        type: 'value', 
                        name: '机构持仓量 (手)', 
                        position: 'left',
                        alignTicks: true,
                        splitLine: {{ lineStyle: {{ type: 'dashed', color: '#eee' }} }},
                        axisLabel: {{ formatter: (value) => value.toLocaleString() }}
                    }},
                    {{ 
                        type: 'value', 
                        name: hasPrice ? priceAxisName : '无数据', 
                        position: 'right',
                        alignTicks: true,
                        splitLine: {{ show: false }},
                        scale: true 
                    }}
                ],
                series: [
                    {{ name: '多头 (Long)', type: 'line', yAxisIndex: 0, data: data.longs, itemStyle: {{color: '#d62728'}}, smooth: true, showSymbol: false, lineStyle: {{width: 2.5}} }},
                    {{ name: '空头 (Short)', type: 'line', yAxisIndex: 0, data: data.shorts, itemStyle: {{color: '#2ca02c'}}, smooth: true, showSymbol: false, lineStyle: {{width: 2.5}} }},
                    {{ name: '净持仓 (Net)', type: 'bar', yAxisIndex: 0, data: data.nets, barMaxWidth: 40, itemStyle: {{ color: (p) => p.data >= 0 ? 'rgba(68, 114, 196, 0.45)' : 'rgba(255, 127, 14, 0.45)' }}, label: {{ show: false }} }},
                    {{ 
                        name: priceAxisName, 
                        type: 'line', 
                        yAxisIndex: 1, 
                        data: data.prices, 
                        itemStyle: {{color: '#8A2BE2'}}, 
                        smooth: true, 
                        showSymbol: true, 
                        symbolSize: 7,
                        lineStyle: {{width: 3, type: 'solid', shadowColor: 'rgba(138, 43, 226, 0.3)', shadowBlur: 8}},
                        connectNulls: true
                    }}
                ]
            }};
            myChart.setOption(option, true);
        }}

        document.getElementById('assetSearch').oninput = (e) => renderAssetList(e.target.value);

        renderAssetList();
        if (assetList.length > 0) {{
            const defaultAsset = assetList.includes('欧元/美元') ? '欧元/美元' : assetList[0];
            const btns = Array.from(document.querySelectorAll('.asset-btn'));
            const targetBtn = btns.find(b => b.innerText === defaultAsset) || btns[0];
            selectAsset(defaultAsset, targetBtn);
        }}

        window.onresize = () => myChart.resize();
    </script>
</body>
</html>
    """
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html_template)
    print(f"\n🎉 完美收工！全资产精确量价面板已生成: {OUTPUT_FILE}")

def main():
    files = sorted(glob.glob(HTML_PATTERN))
    if not files:
        print("❌ 未找到 HTML 文件，请检查当前目录。")
        return
        
    all_data = []
    try:
        for i, f in enumerate(files, 1):
            sys.stdout.write(f"\r⏳ 解析本地报告: [{i}/{len(files)}] {os.path.basename(f)}")
            sys.stdout.flush()
            all_data.extend(parse_html_file(f))
    except KeyboardInterrupt:
        print("\n🛑 解析中断，处理已读取的数据...")
    
    if all_data:
        df = pd.DataFrame(all_data)
        df['Date'] = pd.to_datetime(df['Date'])
        
        # 调用智能分类数据拉取引擎
        df = enrich_with_prices(df)
        generate_dashboard(df)
    else:
        print("\n❌ 未提取到任何有效数据。")

if __name__ == "__main__":
    main()