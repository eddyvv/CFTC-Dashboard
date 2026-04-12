import os
import glob
import re
import json
import sys
import pandas as pd
from bs4 import BeautifulSoup

# ================= 配置区 =================
HTML_PATTERN = "cftc_持仓报告_*.html"
OUTPUT_FILE = "cftc_历史趋势_Dashboard.html"
# ==========================================

def clean_number(text):
    """提取文本中的纯数字（保留负号）"""
    if not text:
        return 0
    # 移除千位分隔符等无关字符，只保留数字和负号
    cleaned = re.sub(r'[^\d\-]', '', text)
    try:
        return int(cleaned) if cleaned and cleaned != '-' else 0
    except ValueError:
        return 0

def parse_html_file(filepath):
    """解析单个CFTC HTML文件并提取核心数据"""
    # 从文件名提取日期
    match = re.search(r'\d{4}-\d{2}-\d{2}', filepath)
    if not match:
        return []
    date_str = match.group()
    
    data_list = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            
            # 找到所有表格行
            for row in soup.find_all('tr'):
                cols = row.find_all('td')
                
                # 过滤掉表头和分类行 (分类行通常带有 colspan)
                if len(cols) >= 11 and not cols[0].has_attr('colspan'):
                    asset_name = cols[0].text.strip()
                    # 根据脚本生成的HTML格式，索引2是净持仓，5是多头，8是空头
                    net_pos = clean_number(cols[2].text)
                    long_pos = clean_number(cols[5].text)
                    short_pos = clean_number(cols[8].text)
                    
                    if asset_name:
                        data_list.append({
                            'Date': date_str,
                            'Asset': asset_name,
                            'Net': net_pos,
                            'Long': long_pos,
                            'Short': short_pos
                        })
    except Exception as e:
        print(f"⚠️ 解析文件 {filepath} 时出错: {e}")
        
    return data_list

def generate_echarts_html(df):
    """将 pandas DataFrame 转换为包含 ECharts 的 HTML"""
    # 获取所有的资产名称
    assets = df['Asset'].unique()
    
    # 修复：将 max() 得到的 Timestamp 转为好看的 YYYY-MM-DD 字符串
    latest_date_str = df['Date'].max().strftime('%Y-%m-%d')
    
    # 准备 HTML 模板
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>CFTC 历史持仓趋势面板</title>
        <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: #f5f7fa; margin: 0; padding: 20px; }
            h1 { text-align: center; color: #333; margin-bottom: 5px; }
            .meta-info { text-align: center; color: #888; font-size: 14px; margin-bottom: 30px; }
            .grid-container { display: flex; flex-wrap: wrap; justify-content: center; gap: 20px; }
            .chart-card { background: #fff; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); padding: 15px; width: 48%; min-width: 600px; box-sizing: border-box; }
            .chart { width: 100%; height: 400px; }
        </style>
    </head>
    <body>
        <h1>CFTC 大类资产多空趋势面板</h1>
        <div class="meta-info">数据更新至: {latest_date} | 图表支持缩放、拖拽和图例点击</div>
        <div class="grid-container">
    """
    
    html_content = html_content.replace("{latest_date}", latest_date_str)
    
    charts_js = ""
    
    # 为每个资产生成一个图表容器和配置
    for i, asset in enumerate(assets):
        asset_data = df[df['Asset'] == asset].sort_values('Date')
        
        # 修复：将 Pandas Timestamp 转换回普通的字符串列表，以便 JSON 序列化
        dates = asset_data['Date'].dt.strftime('%Y-%m-%d').tolist()
        
        longs = asset_data['Long'].tolist()
        shorts = asset_data['Short'].tolist()
        nets = asset_data['Net'].tolist()
        
        chart_id = f"chart_{i}"
        
        # 添加 DOM 容器
        html_content += f"""
            <div class="chart-card">
                <div id="{chart_id}" class="chart"></div>
            </div>
        """
        
        # 组装 ECharts JS 配置
        charts_js += f"""
            var myChart_{i} = echarts.init(document.getElementById('{chart_id}'));
            var option_{i} = {{
                title: {{ text: '{asset} - 多空持仓演变', left: 'center', textStyle: {{color: '#4472C4'}} }},
                tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
                legend: {{ data: ['多头 (Long)', '空头 (Short)', '净持仓 (Net)'], top: 30 }},
                grid: {{ left: '3%', right: '4%', bottom: '15%', containLabel: true }},
                dataZoom: [{{ type: 'slider', start: 0, end: 100 }}, {{ type: 'inside' }}],
                xAxis: {{ type: 'category', boundaryGap: true, data: {json.dumps(dates)} }},
                yAxis: [
                    {{ type: 'value', name: '合约数量', position: 'left', splitLine: {{ lineStyle: {{ type: 'dashed' }} }} }}
                ],
                series: [
                    {{
                        name: '多头 (Long)',
                        type: 'line',
                        itemStyle: {{ color: '#d62728' }}, // 红色
                        lineStyle: {{ width: 3 }},
                        showSymbol: false,
                        smooth: true,
                        data: {json.dumps(longs)}
                    }},
                    {{
                        name: '空头 (Short)',
                        type: 'line',
                        itemStyle: {{ color: '#2ca02c' }}, // 绿色
                        lineStyle: {{ width: 3 }},
                        showSymbol: false,
                        smooth: true,
                        data: {json.dumps(shorts)}
                    }},
                    {{
                        name: '净持仓 (Net)',
                        type: 'bar',
                        itemStyle: {{ 
                            color: function(params) {{ return params.data >= 0 ? 'rgba(68, 114, 196, 0.4)' : 'rgba(255, 127, 14, 0.4)'; }}
                        }},
                        barMaxWidth: 30,
                        data: {json.dumps(nets)}
                    }}
                ]
            }};
            myChart_{i}.setOption(option_{i});
        """

    html_content += """
        </div>
        <script>
            // 渲染所有图表
            setTimeout(function() {
    """
    html_content += charts_js
    html_content += """
            }, 100);
            
            // 响应窗口大小改变
            window.addEventListener('resize', function() {
                var charts = document.querySelectorAll('.chart');
                charts.forEach(function(chartDiv) {
                    var chart = echarts.getInstanceByDom(chartDiv);
                    if (chart) { chart.resize(); }
                });
            });
        </script>
    </body>
    </html>
    """
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"\n🎉 成功生成可视化面板：{OUTPUT_FILE}")
    print(f"👉 请双击打开 {OUTPUT_FILE} 在浏览器中查看！")

def main():
    files = sorted(glob.glob(HTML_PATTERN))
    if not files:
        print(f"❌ 找不到任何符合 {HTML_PATTERN} 的文件。请确认当前目录下包含生成的 HTML 报告。")
        return
        
    print(f"🔍 找到 {len(files)} 份 CFTC 报告，开始解析...")
    
    all_data = []
    
    # Ctrl+C 中断捕获逻辑
    try:
        for i, filepath in enumerate(files, 1):
            sys.stdout.write(f"\r⏳ 正在解析进度: [{i}/{len(files)}] - {os.path.basename(filepath)}...")
            sys.stdout.flush()
            
            parsed_rows = parse_html_file(filepath)
            all_data.extend(parsed_rows)
            
    except KeyboardInterrupt:
        # 当用户按下 Ctrl+C 时触发
        print("\n\n🛑 检测到 [Ctrl+C] 中断！停止读取剩余文件。")
        print("💡 正在使用【已成功读取】的数据为您生成图表，请稍候...")
    
    if not all_data:
        print("\n❌ 提取失败：没有提取到任何有效数据。")
        return
        
    # 将提取的数据转换为 DataFrame 并按日期去重/清理
    df = pd.DataFrame(all_data)
    df['Date'] = pd.to_datetime(df['Date'])
    
    print(f"\n📊 正在处理 {len(df['Asset'].unique())} 种大类资产的时间序列...")
    
    # 调用生成 HTML 引擎
    generate_echarts_html(df)

if __name__ == "__main__":
    main()