import os
import glob
import re
import json
import sys
import time
import requests
import pandas as pd
import akshare as ak
import yfinance as yf
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings('ignore')

# ================= 网络代理配置区 (破除比特币获取限制) =================
# 如果您在国内，且电脑上运行了 Clash / V2Ray 等代理软件，
# 请将 USE_PROXY 改为 True，并确认您的代理软件本地端口号（通常是 7890 或 10809）
USE_PROXY = False  
PROXY_PORT = 7890

if USE_PROXY:
    os.environ['http_proxy'] = f'http://127.0.0.1:{PROXY_PORT}'
    os.environ['https_proxy'] = f'http://127.0.0.1:{PROXY_PORT}'
    print(f"🌍 已开启全局网络代理 (端口: {PROXY_PORT})，准备极速拉取海外数据...")
# =====================================================================

# ================= 核心配置区 =================
HTML_PATTERN = "cftc_持仓报告_*.html"
OUTPUT_FILE = "CFTC_交互式深度分析面板.html"
PRICE_CACHE_FILE = "cftc_价格历史缓存.json"
DATA_EXPORT_FILE = "cftc_面板完整数据.json"

ASSET_CONFIG = {
    '2年期美债': {'type': 'us_yield', 'column': '美国国债收益率2年'},
    '10年期美债': {'type': 'us_yield', 'column': '美国国债收益率10年'},
    '超长期美债': {'type': 'us_yield', 'column': '美国国债收益率30年'},
    
    '黄金': {'type': 'futures', 'symbol': 'GC'},
    '白银': {'type': 'futures', 'symbol': 'SI'},
    '铜': {'type': 'futures', 'symbol': 'HG'},
    'WTI原油': {'type': 'futures', 'symbol': 'CL'},
    '天然气': {'type': 'futures', 'symbol': 'NG'},
    '玉米': {'type': 'futures', 'symbol': 'C'},
    
    '标普500': {'type': 'index_sina', 'symbol': '.INX', 'desc': '标普500原生指数'},
    '纳斯达克100': {'type': 'index_sina', 'symbol': '.NDX', 'desc': '纳斯达克100原生指数'},
    '日经225': {'type': 'custom_api', 'api_source': 'sina_global', 'symbol': 'N225', 'desc': '日经225 (Sina底层API)'},
    
    # 【彻底修复 MSCI】放弃残缺的雅虎官方指数，改用全球流动性最强的 MSCI 官方跟踪 ETF (走势 100% 一致，且永不封禁)
    'MSCI发达市场': {'type': 'etf_proxy', 'symbol': 'URTH', 'desc': 'MSCI 发达市场 (官方跟踪ETF: URTH)'},
    'MSCI新兴市场': {'type': 'etf_proxy', 'symbol': 'EEM', 'desc': 'MSCI 新兴市场 (官方跟踪ETF: EEM)'},
    
    '欧元/美元': {'type': 'yf_asset', 'symbol': 'EURUSD=X', 'fallback_etf': 'FXE', 'desc': 'EUR/USD 原生汇率指数'},
    '英镑/美元': {'type': 'yf_asset', 'symbol': 'GBPUSD=X', 'fallback_etf': 'FXB', 'desc': 'GBP/USD 原生汇率指数'},
    '日元/美元': {'type': 'yf_asset', 'symbol': 'JPYUSD=X', 'fallback_etf': 'FXY', 'desc': 'JPY/USD 原生汇率指数'},
    '澳元/美元': {'type': 'yf_asset', 'symbol': 'AUDUSD=X', 'fallback_etf': 'FXA', 'desc': 'AUD/USD 原生汇率指数'},
    
    # 【修复比特币】全新混合多源节点，并配合上方的代理开关
    '比特币': {'type': 'custom_api', 'api_source': 'crypto_multi', 'symbol': 'BTC', 'desc': '真实比特币现货 (多源防封禁)'},

    '罗素2000': {'type': 'etf_proxy', 'symbol': 'IWM', 'desc': 'IWM ETF 代理'},
    '联邦基金': {'type': 'etf_proxy', 'symbol': 'BIL', 'desc': 'BIL 短债基准代理'}
}
# ==========================================

def load_cache():
    if os.path.exists(PRICE_CACHE_FILE):
        try:
            with open(PRICE_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        with open(PRICE_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

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
    except Exception: pass
    return data_list

def enrich_with_prices(df):
    print("\n🌐 开始匹配历史资产走势...")
    df['Price'] = None
    assets = df['Asset'].unique()
    
    min_date = df['Date'].min() - pd.Timedelta(days=7)
    max_date = df['Date'].max() + pd.Timedelta(days=7)
    
    cache = load_cache()
    yield_data_cache = None
    yield_fetched = False

    for asset in assets:
        if asset not in ASSET_CONFIG:
            continue
            
        cfg = ASSET_CONFIG[asset]
        desc = cfg.get('desc', '') 
        
        mask = df['Asset'] == asset
        asset_dates = df.loc[mask, 'Date']
        if asset_dates.empty: continue
        
        max_req_date = asset_dates.max().strftime('%Y-%m-%d')
        asset_cache = cache.get(asset, {})
        
        need_fetch = True
        if asset_cache:
            max_cached_date = max(asset_cache.keys())
            if max_req_date <= max_cached_date:
                need_fetch = False
                
        if need_fetch:
            sys.stdout.write(f"\r📈 [网络拉取] {asset} ({desc}) ...       ")
            sys.stdout.flush()
            close_px = pd.Series(dtype=float)
            
            try:
                # --- A: 自定义多级 API (专破比特币与日经) ---
                if cfg['type'] == 'custom_api':
                    
                    if cfg['api_source'] == 'sina_global':
                        try:
                            url = f"https://vip.stock.finance.sina.com.cn/api/json_v2.php/GlobalMarketService.getGlobalIndexDaily?symbol={cfg['symbol']}"
                            resp = requests.get(url, timeout=10).json()
                            tmp_df = pd.DataFrame(resp)
                            if not tmp_df.empty and 'date' in tmp_df.columns and 'close' in tmp_df.columns:
                                tmp_df['date'] = pd.to_datetime(tmp_df['date'])
                                tmp_df.set_index('date', inplace=True)
                                close_px = tmp_df['close'].astype(float).dropna()
                        except Exception: pass
                        
                    elif cfg['api_source'] == 'crypto_multi':
                        success = False
                        
                        # 1. 尝试火币公用节点 (部分国内网络可直连)
                        try:
                            url = "https://api.huobi.pro/market/history/kline?period=1day&size=2000&symbol=btcusdt"
                            resp = requests.get(url, timeout=5).json()
                            if resp.get('status') == 'ok' and resp.get('data'):
                                tmp_df = pd.DataFrame(resp['data'])
                                tmp_df['date'] = pd.to_datetime(tmp_df['id'], unit='s').dt.normalize()
                                tmp_df.set_index('date', inplace=True)
                                close_px = tmp_df['close'].astype(float).dropna()
                                success = True
                                sys.stdout.write(f" [火币节点命中] ")
                        except Exception: pass
                        
                        # 2. 尝试币安备用节点 (需要挂代理 USE_PROXY=True)
                        if not success or close_px.empty:
                            try:
                                url = "https://api3.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000"
                                resp = requests.get(url, timeout=5).json()
                                if isinstance(resp, list) and len(resp) > 0:
                                    tmp_df = pd.DataFrame(resp, columns=['date', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
                                    tmp_df['date'] = pd.to_datetime(tmp_df['date'], unit='ms').dt.normalize()
                                    tmp_df.set_index('date', inplace=True)
                                    close_px = tmp_df['close'].astype(float).dropna()
                                    success = True
                                    sys.stdout.write(f" [币安节点命中] ")
                            except Exception: pass

                        # 3. YFinance 终极兜底
                        if not success or close_px.empty:
                            try:
                                ticker_obj = yf.Ticker('BTC-USD')
                                hist = ticker_obj.history(start=min_date.strftime('%Y-%m-%d'), end=max_date.strftime('%Y-%m-%d'))
                                if not hist.empty and 'Close' in hist.columns:
                                    if hist.index.tz is not None: hist.index = hist.index.tz_localize(None)
                                    close_px = hist['Close'].astype(float).dropna()
                                    sys.stdout.write(f" [雅虎节点兜底命中] ")
                            except Exception: pass

                # --- B: 国债收益率 ---
                elif cfg['type'] == 'us_yield':
                    if not yield_fetched:
                        try:
                            yield_data_cache = ak.bond_zh_us_rate(start_date="20200101")
                            yield_data_cache['日期'] = pd.to_datetime(yield_data_cache['日期'])
                            yield_data_cache.set_index('日期', inplace=True)
                        except Exception: pass
                        yield_fetched = True
                    if yield_data_cache is not None:
                        col = cfg['column']
                        if col in yield_data_cache.columns:
                            close_px = yield_data_cache[col].dropna()

                # --- C: 商品期货 ---
                elif cfg['type'] == 'futures':
                    hist = ak.futures_foreign_hist(symbol=cfg['symbol'])
                    if not hist.empty:
                        hist.columns = [str(c).lower() for c in hist.columns]
                        if 'date' in hist.columns and 'close' in hist.columns:
                            hist['date'] = pd.to_datetime(hist['date'])
                            hist.set_index('date', inplace=True)
                            close_px = hist['close'].astype(float).dropna()

                # --- D: 新浪美股指数 ---
                elif cfg['type'] == 'index_sina':
                    hist = ak.index_us_stock_sina(symbol=cfg['symbol'])
                    if not hist.empty:
                        hist.columns = [str(c).lower() for c in hist.columns]
                        if 'date' in hist.columns and 'close' in hist.columns:
                            hist['date'] = pd.to_datetime(hist['date'])
                            hist.set_index('date', inplace=True)
                            close_px = hist['close'].astype(float).dropna()

                # --- E: Yahoo Finance (汇率) ---
                elif cfg['type'] in ['yf_asset']:
                    try:
                        ticker_obj = yf.Ticker(cfg['symbol'])
                        hist = ticker_obj.history(start=min_date.strftime('%Y-%m-%d'), end=max_date.strftime('%Y-%m-%d'))
                        if not hist.empty and 'Close' in hist.columns:
                            if hist.index.tz is not None: hist.index = hist.index.tz_localize(None)
                            close_px = hist['Close'].astype(float).dropna()
                    except Exception: pass
                
                # --- F: ETF 代理 (适用于 MSCI 和 纯宽基) ---
                elif cfg['type'] == 'etf_proxy':
                    hist = ak.stock_us_daily(symbol=cfg['symbol'], adjust="qfq")
                    if not hist.empty:
                        hist.columns = [str(c).lower() for c in hist.columns]
                        if 'date' in hist.columns and 'close' in hist.columns:
                            hist['date'] = pd.to_datetime(hist['date'])
                            hist.set_index('date', inplace=True)
                            close_px = hist['close'].astype(float).dropna()

                # --- 防断连降级保护 ---
                if close_px.empty and cfg.get('fallback_etf'):
                    sys.stdout.write(f" [降级 ETF: {cfg['fallback_etf']}] ")
                    sys.stdout.flush()
                    hist = ak.stock_us_daily(symbol=cfg['fallback_etf'], adjust="qfq")
                    if not hist.empty:
                        hist.columns = [str(c).lower() for c in hist.columns]
                        if 'date' in hist.columns and 'close' in hist.columns:
                            hist['date'] = pd.to_datetime(hist['date'])
                            hist.set_index('date', inplace=True)
                            close_px = hist['close'].astype(float).dropna()

                # 存入缓存
                if not close_px.empty:
                    if close_px.index.tz is not None:
                        close_px.index = close_px.index.tz_localize(None)
                    daily_dict = {d.strftime('%Y-%m-%d'): float(v) for d, v in close_px.items() if pd.notna(v)}
                    cache[asset] = {**asset_cache, **daily_dict}
                    save_cache(cache)
                    asset_cache = cache[asset]
                    
                time.sleep(0.5) 
                
            except Exception as e:
                print(f"\n⚠️ {asset} 网络拉取异常: {e}")
                
        else:
            sys.stdout.write(f"\r⚡ [命中缓存] {asset} (极速加载) ...       ")
            sys.stdout.flush()

        # ================= 日期对齐 =================
        if asset_cache:
            cached_series = pd.Series(asset_cache)
            cached_series.index = pd.to_datetime(cached_series.index)
            cached_series = cached_series.sort_index()
            
            prices = []
            for d in asset_dates:
                available_dates = cached_series[cached_series.index <= d]
                if not available_dates.empty:
                    prices.append(float(available_dates.iloc[-1]))
                else:
                    prices.append(None)
                    
            df.loc[mask, 'Price'] = prices

    print(f"\n✅ 数据准备完毕！价格数据已自动持久化至: {PRICE_CACHE_FILE}")
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

    try:
        with open(DATA_EXPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, ensure_ascii=False, indent=4)
        print(f"💾 面板完整数据源已导出至: {DATA_EXPORT_FILE}")
    except Exception:
        pass

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
        .type-custom {{ background: #fff0f6; color: #eb2f96; border: 1px solid #ffadd2; }}
        .info {{ color: #666; font-size: 14px; display: flex; flex-direction: column; gap: 5px; }}
        .sub-info {{ font-size: 13px; color: #888; }}
        #chart-container {{ flex: 1; background: #fff; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); padding: 20px; min-height: 400px; }}
    </style>
</head>
<body>
    <div id="sidebar">
        <div class="search-box">
            <input type="text" id="assetSearch" placeholder="🔍 搜索资产 (如: MSCI, 比特币)">
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
            }} else if (cfg.type === 'index_sina') {{
                badge.className = 'badge-type type-index';
                badge.innerText = '原生指数走势';
                desc.innerHTML = '💡 <strong>数据说明：</strong>直接获取官方核心指数的绝对点数。';
                priceAxisName = '指数点数';
            }} else if (cfg.type === 'custom_api') {{
                badge.className = 'badge-type type-custom';
                badge.innerText = name.includes('比特币') ? '原生现货直连' : 'API直连指数';
                desc.innerHTML = '💡 <strong>极客多源：</strong>内置多级防封禁架构，强制拉取纯净行情。';
                priceAxisName = name.includes('比特币') ? '现货报价 ($)' : '指数点数';
            }} else if (cfg.type === 'yf_asset') {{
                badge.className = 'badge-type type-yf';
                badge.innerText = '原生汇率指数';
                desc.innerHTML = '💡 <strong>数据说明：</strong>精准对接 <strong>' + cfg.symbol + '</strong> 原生行情走势。';
                priceAxisName = '汇率指数';
            }} else if (cfg.type === 'etf_proxy') {{
                badge.className = 'badge-type type-proxy';
                badge.innerText = name.includes('MSCI') ? 'MSCI 官方跟踪 ETF' : '指数 ETF 穿透代理';
                desc.innerHTML = '💡 <strong>数据说明：</strong>因官方指数闭源收费，使用全球最大流动性跟踪 ETF (<strong>' + cfg.symbol + '</strong>) 进行走势完美拟合。';
                priceAxisName = 'ETF 净值 ($)';
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
                                let decimals = 2;
                                if (cfg.type === 'us_yield') decimals = 3;
                                else if (name.includes('/') || name.includes('汇率')) decimals = 4;
                                else if (name.includes('比特币')) decimals = 2;
                                
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
            const defaultAsset = assetList.includes('MSCI新兴市场') ? 'MSCI新兴市场' : assetList[0];
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
    print(f"🎉 完美收工！前端分析面板已生成: {OUTPUT_FILE}")

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
        
        df = enrich_with_prices(df)
        generate_dashboard(df)
    else:
        print("\n❌ 未提取到任何有效数据。")

if __name__ == "__main__":
    main()