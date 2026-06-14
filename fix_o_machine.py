#!/usr/bin/env python3
"""O机器环境修复脚本 - 解决已知问题
需要以管理员权限运行
"""

import subprocess
import sys
import os

def run(cmd, check=True):
    print(f"  > {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.stdout.strip():
        print(f"    {r.stdout.strip()[:200]}")
    if r.returncode != 0 and r.stderr.strip():
        print(f"    ERR: {r.stderr.strip()[:200]}")
    if check and r.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return r

def main():
    print("=" * 60)
    print("O机器环境修复")
    print("=" * 60)

    # Fix 1: Git dubious ownership
    print("\n[1] 修复 Git dubious ownership")
    print("    G:\\AI\\FIFA 仓库所有者与当前用户不匹配")
    try:
        run(["git", "config", "--global", "safe.directory", r"G:\AI\FIFA"], check=False)
        print("    已添加 G:\\AI\\FIFA 到 safe.directory")
        # 验证
        r = run(["git", "-C", r"G:\AI\FIFA", "status", "--short"], check=False)
        if r.returncode == 0:
            print("    Git 操作正常")
        else:
            print("    Git 仍报错，尝试通配符方式")
            run(["git", "config", "--global", "--add", "safe.directory", "*"], check=False)
    except Exception as e:
        print(f"    修复失败: {e}")

    # Fix 2: Check cloudflared process
    print("\n[2] 检查 cloudflared 进程")
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                          capture_output=True, text=True, timeout=10)
        if "cloudflared.exe" in r.stdout:
            print("    cloudflared 正在运行")
        else:
            print("    cloudflared 未运行！需要启动隧道")
    except Exception as e:
        print(f"    检查失败: {e}")

    # Fix 3: Check all service ports
    print("\n[3] 检查各服务端口")
    ports = {
        8080: "Gateway",
        8081: "云色",
        8082: "云影",
        8083: "云音",
        8084: "云册",
        8085: "云听",
        8086: "FIFA",
    }
    for port, name in ports.items():
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', port))
            s.close()
            if result == 0:
                print(f"    :{port} {name} - 运行中")
            else:
                print(f"    :{port} {name} - 未启动!")
        except Exception as e:
            print(f"    :{port} {name} - 检测失败: {e}")

    # Fix 4: Test gateway -> FIFA routing
    print("\n[4] 测试 Gateway -> FIFA 路由")
    try:
        r = subprocess.run(
            ['curl', '-s', '-o', 'NUL', '-w', '%{http_code}',
             'http://localhost:8080/fifa/'],
            capture_output=True, text=True, timeout=10
        )
        code = r.stdout.strip()
        if code == '200':
            print(f"    /fifa/ -> HTTP {code} (正常)")
        else:
            print(f"    /fifa/ -> HTTP {code} (异常!)")
    except FileNotFoundError:
        # curl not available, use Python
        try:
            import urllib.request
            resp = urllib.request.urlopen('http://localhost:8080/fifa/', timeout=5)
            print(f"    /fifa/ -> HTTP {resp.status} (正常)")
        except Exception as e:
            print(f"    /fifa/ -> 错误: {e}")
    except Exception as e:
        print(f"    测试失败: {e}")

    # Fix 5: Test default route (no prefix)
    print("\n[5] 测试 Gateway 默认路由 -> FIFA")
    try:
        import urllib.request
        resp = urllib.request.urlopen('http://localhost:8080/', timeout=5)
        print(f"    / -> HTTP {resp.status} (正常)")
    except Exception as e:
        print(f"    / -> 错误: {e}")

    # Fix 6: Test direct FIFA access
    print("\n[6] 测试直接访问 FIFA 8086")
    try:
        import urllib.request
        resp = urllib.request.urlopen('http://localhost:8086/', timeout=5)
        print(f"    localhost:8086 -> HTTP {resp.status} (正常)")
    except Exception as e:
        print(f"    localhost:8086 -> 错误: {e}")

    # Fix 7: Test LAN IP access (this is the phone 404 issue)
    print("\n[7] 测试 LAN IP 访问 FIFA")
    try:
        import urllib.request
        resp = urllib.request.urlopen('http://192.168.0.10:8086/', timeout=5)
        print(f"    192.168.0.10:8086 -> HTTP {resp.status} (正常)")
    except Exception as e:
        print(f"    192.168.0.10:8086 -> 错误: {e}")

    print("\n" + "=" * 60)
    print("诊断完成。请将结果截图发给塞斯。")
    print("=" * 60)


if __name__ == '__main__':
    main()
