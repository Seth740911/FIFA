"""
O机 SMB 共享配置脚本
以管理员身份运行，将 G:\AI 共享给局域网
"""
import subprocess
import sys

def run(cmd):
    print(f"  > {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0 and r.stderr:
        print(f"    ERROR: {r.stderr.strip()}")
    elif r.stdout:
        print(f"    {r.stdout.strip()}")
    return r.returncode == 0

def main():
    print("=" * 50)
    print("  O机 SMB 共享配置")
    print("=" * 50)

    share_path = r"G:\AI"
    share_name = "AI"

    # 1. 确保目录存在
    import os
    os.makedirs(share_path, exist_ok=True)
    print(f"\n[1] 共享目录: {share_path}")

    # 2. 删除已有同名共享(避免冲突)
    print(f"\n[2] 清理旧共享 '{share_name}'...")
    run(f'net share {share_name} /delete /y 2>nul')

    # 3. 创建共享
    print(f"\n[3] 创建共享 '{share_name}'...")
    run(f'net share {share_name}="{share_path}" /grant:Everyone,FULL')

    # 4. 防火墙放行SMB
    print(f"\n[4] 防火墙放行 SMB (445端口)...")
    run('netsh advfirewall firewall delete rule name="SMB-in-445" 2>nul')
    run('netsh advfirewall firewall add rule name="SMB-in-445" dir=in action=allow protocol=TCP localport=445')

    # 5. 验证
    print(f"\n[5] 验证共享...")
    run(f'net share {share_name}')

    print()
    print("  配置完成!")
    print(f"  N机访问: \\\\192.168.0.10\\{share_name}")
    print(f"  N机映射: net use Z: \\\\192.168.0.10\\{share_name} /persistent:yes")
    print()

if __name__ == "__main__":
    if not sys.argv[0].endswith("python.exe"):
        print("请以管理员身份运行此脚本!")
    main()
