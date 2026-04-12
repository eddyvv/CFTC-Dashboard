import os
import subprocess
import pandas as pd
from datetime import datetime, timedelta
import time
import sys

# ================= 配置区 =================
# 主脚本的文件名
MASTER_SCRIPT = "cftc_position_analysis.py"
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

def run_task(start_date, end_date):
    tuesdays = get_tuesdays(start_date, end_date)
    total = len(tuesdays)
    
    if total == 0:
        print(f"⚠️ 在 {start_date} 到 {end_date} 期间没有找到任何周二。")
        return

    # 确保输出文件夹存在
    output_dir = "0_持仓报告"
    os.makedirs(output_dir, exist_ok=True)
        
    print(f"🚀 开始批量获取任务...")
    print(f"📅 目标周期: {start_date} 至 {end_date}")
    print(f"📊 共有 {total} 个周二待处理")
    print("💡 提示: 按 Ctrl+C 可安全停止当前任务，已生成的文件不会丢失。\n")

    for i, date_str in enumerate(tuesdays, 1):
        # 检查是否已经存在该日期的结果文件
        expected_file = os.path.join(output_dir, f"cftc_持仓报告_{date_str}.html")
        if os.path.exists(expected_file):
            print(f"[{i}/{total}] ⏭️ 跳过 {date_str} (文件已存在)")
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

def print_help():
    print("=====================================================")
    print("CFTC 持仓分析批量执行工具 (Batch Executor)")
    print("=====================================================")
    print("用法:")
    print("  python cftc_batch_executor.py <起始时间> <结束时间>")
    print("\n参数说明:")
    print("  <起始时间> : 格式为 YYYY-MM-DD (如 2025-01-01)")
    print("  <结束时间> : 格式为 YYYY-MM-DD (如 2026-04-10)")
    print("\n示例:")
    print("  python cftc_batch_executor.py 2025-01-01 2026-04-10")
    print("=====================================================")

if __name__ == "__main__":
    # 检查命令行参数数量，sys.argv[0] 是脚本本身，所以需要 3 个参数
    if len(sys.argv) != 3:
        print_help()
        sys.exit(1)

    start_date_str = sys.argv[1]
    end_date_str = sys.argv[2]

    # 校验日期格式是否正确
    try:
        datetime.strptime(start_date_str, "%Y-%m-%d")
        datetime.strptime(end_date_str, "%Y-%m-%d")
    except ValueError:
        print("❌ 错误: 日期格式不正确！请必须使用 YYYY-MM-DD 的格式（例如 2025-01-01）。\n")
        print_help()
        sys.exit(1)
        
    # 校验起始时间是否早于结束时间
    if start_date_str > end_date_str:
        print("❌ 错误: <起始时间> 不能晚于 <结束时间>！\n")
        sys.exit(1)

    # 开始执行任务
    run_task(start_date_str, end_date_str)