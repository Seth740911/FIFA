"""
尚唯全家桶 + FIFA 一键启动脚本
O机(192.168.0.10)常开服务用
双击运行即可启动全部6个服务
"""
import subprocess
import sys
import os

PROJECTS = [
    {"name": "云色", "dir": r"G:\AI\GL",  "port": 8081},
    {"name": "云影", "dir": r"G:\AI\MV",  "port": 8082},
    {"name": "云音", "dir": r"G:\AI\YY",  "port": 8083},
    {"name": "云册", "dir": r"G:\AI\PZ",  "port": 8084},
    {"name": "云听", "dir": r"G:\AI\QY",  "port": 8085},
    {"name": "FIFA", "dir": r"G:\AI\FIFA", "port": 8086},
    {"name": "APK下载", "dir": r"G:\AI\APK", "port": 8088},
]

def main():
    print("=" * 50)
    print("  尚唯全家桶 + FIFA 一键启动")
    print("=" * 50)

    procs = []
    for p in PROJECTS:
        server_path = os.path.join(p["dir"], "server.py")
        if not os.path.exists(server_path):
            print(f"  [SKIP] {p['name']} - {server_path} 不存在")
            continue
        try:
            proc = subprocess.Popen(
                [sys.executable, server_path],
                cwd=p["dir"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            procs.append({"proc": proc, "info": p})
            print(f"  [OK]   {p['name']} :{p['port']}  PID={proc.pid}")
        except Exception as e:
            print(f"  [FAIL] {p['name']} - {e}")

    print()
    print(f"  已启动 {len(procs)}/{len(PROJECTS)} 个服务")
    print("  各服务在独立窗口运行，关闭窗口即停止服务")
    print("  本窗口可安全关闭")
    print()

if __name__ == "__main__":
    main()
