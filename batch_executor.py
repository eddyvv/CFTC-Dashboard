import os
import subprocess
import pandas as pd
from datetime import datetime, timedelta
import time
import sys

# ================= 配置区 =================
# 主脚本的文件名
MASTER_SCRIPT = "cftc_持仓分析.py"
# 想要获取的历史范围
START_DATE = "2025-01-01"
END_DATE = "2026-04-10"  # 或者使用 datetime.now().strftime('%Y-%m-%d')
# 每次请求之间的间隔（秒），防止被API封禁或请求过快
SLEEP_INTERVAL = 2
# ==========================================

def get_tuesdays(start_str, end_str):
    """计算日期范围内所有的周二"""
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    
    tuesdays = []
    curr = start
    while curr <= end:
        if curr.weekday() == 1:  # 1 代表 Tuesday
            tuesdays.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)
    return tuesdays

def run_task():
    tuesdays = get_tuesdays(START_DATE, END_DATE)
    total = len(tuesdays)
    
    print(f"🚀 开始批量获取任务...")
    print(f"📅 目标周期: {START_DATE} 至 {END_DATE}")
    print(f"📊 共有 {total} 个周二待处理")
    print("💡 提示: 按 Ctrl+C 可安全停止当前任务，已生成的文件不会丢失。\n")

    for i, date_str in enumerate(tuesdays, 1):
        # 检查是否已经存在该日期的结果文件（可选，防止重复运行）
        # 你的主脚本输出格式是: cftc_持仓报告_YYYY-MM-DD.html
        expected_file = f"cftc_持仓报告_{date_str}.html"
        if os.path.exists(expected_file):
            print(f"[{i}/{total}] 跳过 {date_str} (文件已存在)")
            continue

        print(f"[{i}/{total}] 正在获取 {date_str} 的数据...")
        
        try:
            # 调用你的原始脚本
            # 使用 sys.executable 确保使用相同的 Python 环境
            result = subprocess.run(
                [sys.executable, MASTER_SCRIPT, "--date", date_str],
                capture_output=False, # 让主脚本的进度直接显示在屏幕上
                text=True
            )
            
            if result.returncode == 0:
                print(f"✅ {date_str} 处理成功")
            else:
                print(f"❌ {date_str} 处理失败 (返回码: {result.returncode})")
            
            # 即使失败也等一下，规避网络抖动或API限制
            time.sleep(SLEEP_INTERVAL)

        except KeyboardInterrupt:
            print("\n\n🛑 检测到 Ctrl+C。正在安全退出...")
            print("已完成的任务数据已保存在本地目录。")
            sys.exit(0)
        except Exception as e:
            print(f"⚠️ 运行 {date_str} 时发生未知错误: {e}")
            print("将在 5 秒后尝试下一个日期...")
            time.sleep(5)

if __name__ == "__main__":
    run_task()