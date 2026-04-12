import os
import glob
import re
import json
import sys
import pandas as pd
from bs4 import BeautifulSoup

# ================= 配置区 =================
HTML_PATTERN = "cftc_持仓报告_*.html"
OUTPUT_FILE = "CFTC_交互式深度分析面板.html"
# ==========================================

def clean_number(text):
    """清理并转换数字字符串"""
    if not text: return 0
    cleaned = re.sub(r'[^\d\-]', '', text)
    try:
        return int(cleaned) if cleaned and cleaned != '-' else 0
    except ValueError:
        return 0

def parse_html_file(filepath):
    """解析单个 HTML 报告"""
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
        print(f"⚠️ 解析 {filepath} 失败: {e}")
    return data_list

def generate_dashboard(df):
    """生成带按钮切换功能的 ECharts HTML"""
    # 格式化日期并排序
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values(['Asset', 'Date'])
    
    # 转换为 JSON 格式供前端使用
    assets = sorted(df['Asset'].unique().tolist())
    full_data = {}
    for asset in assets:
        sub = df[df['Asset'] == asset]
        full_data[asset] = {
            'dates': sub['Date'].dt.strftime('%Y-%m-%d').tolist(),
            'longs': sub['Long'].tolist(),
            'shorts': sub['Short'].tolist(),
            'nets': sub['Net'].tolist()
        }

    latest_date = df['Date'].max().strftime('%Y-%m-%d')
    
    html_template = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>CFTC 历史数据交互面板</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        body {{ display: flex; height: 100vh; margin: 0; font-family: sans-serif; background: #f0f2f5; }}
        #sidebar {{ 
            width: 260px; background: #fff; border-right: 1px solid #ddd; 
            display: flex; flex-direction: column; overflow: hidden;
        }}
        .search-box {{ padding: 15px; border-bottom: 1px solid #eee; }}
        #assetSearch {{ width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }}
        #assetList {{ flex: 1; overflow-y: auto; padding: 10px; }}
        .asset-btn {{ 
            width: 100%; text-align: left; padding: 10px 15px; margin-bottom: 5px;
            border: none; background: transparent; cursor: pointer; border-radius: 4px;
            font-size: 14px; color: #333; transition: all 0.2s;
        }}
        .asset-btn:hover {{ background: #e6f7ff; color: #1890ff; }}
        .asset-btn.active {{ background: #1890ff; color: #fff; font-weight: bold; }}
        
        #main {{ flex: 1; display: flex; flex-direction: column; padding: 20px; }}
        header {{ margin-bottom: 20px; background: #fff; padding: 15px 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
        h1 {{ margin: 0; font-size: 20px; color: #1a1a1a; }}
        .info {{ color: #888; font-size: 13px; margin-top: 5px; }}
        #chart-container {{ flex: 1; background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); padding: 20px; }}
    </style>
</head>
<body>
    <div id="sidebar">
        <div class="search-box">
            <input type="text" id="assetSearch" placeholder="搜索资产名称...">
        </div>
        <div id="assetList"></div>
    </div>
    <div id="main">
        <header>
            <h1 id="currentAsset">请选择资产</h1>
            <div class="info">历史数据区间: {df['Date'].min().strftime('%Y-%m-%d')} 至 {latest_date} | 数据来源: CFTC</div>
        </header>
        <div id="chart-container">
            <div id="chart" style="width: 100%; height: 100%;"></div>
        </div>
    </div>

    <script>
        const rawData = {json.dumps(full_data)};
        const assetList = {json.dumps(assets)};
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
            document.getElementById('currentAsset').innerText = name + " - 历史持仓趋势分析";
            
            const data = rawData[name];
            const option = {{
                tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
                legend: {{ data: ['多头 (Long)', '空头 (Short)', '净持仓 (Net)'], top: 0 }},
                grid: {{ left: '3%', right: '4%', bottom: '8%', containLabel: true }},
                dataZoom: [{{ type: 'slider', start: 0, end: 100 }}, {{ type: 'inside' }}],
                xAxis: {{ type: 'category', data: data.dates, boundaryGap: true }},
                yAxis: {{ type: 'value', name: '合约数量', splitLine: {{ lineStyle: {{ type: 'dashed' }} }} }},
                series: [
                    {{ name: '多头 (Long)', type: 'line', data: data.longs, itemStyle: {{color: '#d62728'}}, smooth: true, showSymbol: false, lineStyle: {{width: 3}} }},
                    {{ name: '空头 (Short)', type: 'line', data: data.shorts, itemStyle: {{color: '#2ca02c'}}, smooth: true, showSymbol: false, lineStyle: {{width: 3}} }},
                    {{ name: '净持仓 (Net)', type: 'bar', data: data.nets, 
                       itemStyle: {{ color: (p) => p.data >= 0 ? 'rgba(68, 114, 196, 0.4)' : 'rgba(255, 127, 14, 0.4)' }} 
                    }}
                ]
            }};
            myChart.setOption(option, true);
        }}

        // 搜索功能
        document.getElementById('assetSearch').oninput = (e) => renderAssetList(e.target.value);

        // 初始化
        renderAssetList();
        if (assetList.length > 0) {{
            const firstBtn = document.querySelector('.asset-btn');
            selectAsset(assetList[0], firstBtn);
        }}

        window.onresize = () => myChart.resize();
    </script>
</body>
</html>
    """
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html_template)
    print(f"\n🎉 交互式面板已生成: {OUTPUT_FILE}")

def main():
    files = sorted(glob.glob(HTML_PATTERN))
    if not files:
        print("❌ 未找到 HTML 文件")
        return
        
    all_data = []
    try:
        for i, f in enumerate(files, 1):
            sys.stdout.write(f"\r⏳ 解析进度: [{i}/{len(files)}] {os.path.basename(f)}")
            all_data.extend(parse_html_file(f))
    except KeyboardInterrupt:
        print("\n🛑 用户中断，正在生成部分数据图表...")
    
    if all_data:
        df = pd.DataFrame(all_data)
        generate_dashboard(df)
    else:
        print("\n❌ 无有效数据")

if __name__ == "__main__":
    main()