#!/usr/bin/env python3
"""
build_fifa.py — FIFA 2026 APK Builder (clean rewrite)
=====================================================
WebView APK for 2026 World Cup Guide.

Connection strategy (JavaScript in launcher page):
  1. XHR probe LAN:8086 (2s timeout) — fastest, works at home
  2. DNS TXT lookup for cloudflare tunnel — works on any network
  3. Fallback to WAN domain direct — last resort

Prerequisites (in G:\AI\SVAPK\apk-build\):
  - aapt2.exe
  - d8/d8.jar
  - android-framework.jar
  - debug.keystore  (password: android, alias: androiddebugkey)

Icon: G:\AI\FIFA\WC26_Logo.png
Output: G:\AI\APK\fifa.apk
"""

import os, sys, subprocess, shutil, tempfile, textwrap

# ── Paths ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR  = r"G:\AI\SVAPK\apk-build"
AAPT2      = os.path.join(TOOLS_DIR, "aapt2.exe")
D8_JAR     = os.path.join(TOOLS_DIR, "d8", "d8.jar")
FRAMEWORK  = os.path.join(TOOLS_DIR, "android-framework.jar")
KEYSTORE   = os.path.join(TOOLS_DIR, "debug.keystore")
KEY_PASS   = "android"
KEY_ALIAS  = "androiddebugkey"
JAVA       = os.path.join(r"G:\AI\.temp\jdk8", "bin", "java.exe")

FIFA_DIR   = r"G:\AI\FIFA"
LOGO_SRC   = os.path.join(FIFA_DIR, "WC26_Logo.png")
OUT_APK    = r"G:\AI\APK\fifa.apk"

HOST_LAN = "192.168.0.10"
HOST_WAN = "sethshi.dynv6.net"
FIFA_PORT = 8086

# ── Android stub sources (minimal compilation stubs) ───────────────────────────

STUB_FILES = {
    "Context.java": """package android.content;
public class Context {
    public static final String ACTION_VIEW = "android.intent.action.VIEW";
    public static final int FLAG_ACTIVITY_NEW_TASK = 0x10000000;
    public android.content.SharedPreferences getSharedPreferences(String n, int m) { return null; }
    public String getCacheDir() { return null; }
    public String getPackageName() { return ""; }
    public void startActivity(Intent i) {}
}""",
    "ContextWrapper.java": """package android.content;
public class ContextWrapper extends Context {}""",
    "ContextThemeWrapper.java": """package android.view;
public class ContextThemeWrapper extends android.content.ContextWrapper {}""",
    "SharedPreferences.java": """package android.content;
public class SharedPreferences {
    public String getString(String k, String d) { return d; }
    public Editor edit() { return null; }
    public interface Editor {
        Editor putString(String k, String v);
        boolean commit();
    }
}""",
    "Activity.java": """package android.app;
public class Activity extends android.view.ContextThemeWrapper {
    public void setContentView(android.view.View v) {}
    public void setContentView(int id) {}
    public android.view.Window getWindow() { return null; }
    public void runOnUiThread(Runnable r) {}
    public void setRequestedOrientation(int o) {}
    public void onBackPressed() {}
    public void startActivity(android.content.Intent i) {}
    public boolean requestWindowFeature(int f) { return true; }
    protected void onCreate(android.os.Bundle b) {}
    protected void onResume() {}
    protected void onPause() {}
    protected void onDestroy() {}
}""",
    "View.java": """package android.view;
public class View {
    public static final int VISIBLE = 0;
    public static final int GONE = 8;
    private android.content.Context mContext;
    public View(android.content.Context c) { mContext = c; }
    public android.content.Context getContext() { return mContext; }
    public void setLayoutParams(android.view.ViewGroup.LayoutParams p) {}
    public android.view.ViewGroup.LayoutParams getLayoutParams() { return null; }
}""",
    "ViewGroup.java": """package android.view;
public class ViewGroup extends View {
    public static class LayoutParams {
        public static final int MATCH_PARENT = -1;
        public static final int WRAP_CONTENT = -2;
        public int width, height;
        public LayoutParams(int w, int h) { width = w; height = h; }
    }
    public ViewGroup(android.content.Context c) { super(c); }
    public void addView(View child) {}
    public void addView(View child, LayoutParams params) {}
    public void removeView(View child) {}
}""",
    "FrameLayout.java": """package android.widget;
public class FrameLayout extends android.view.ViewGroup {
    public FrameLayout(android.content.Context c) { super(c); }
    public static class LayoutParams extends android.view.ViewGroup.LayoutParams {
        public LayoutParams(int w, int h) { super(w, h); }
    }
}""",
    "WebView.java": """package android.webkit;
public class WebView extends android.view.View {
    public WebView(android.content.Context c) { super(c); }
    public void loadUrl(String u) {}
    public void loadDataWithBaseURL(String b, String d, String m, String e, String h) {}
    public void setWebViewClient(WebViewClient c) {}
    public void setWebChromeClient(WebChromeClient c) {}
    public WebSettings getSettings() { return null; }
    public void goBack() {}
    public boolean canGoBack() { return false; }
    public String getUrl() { return null; }
    public void resumeTimers() {}
    public void pauseTimers() {}
    public void destroy() {}
    public void evaluateJavascript(String s, ValueCallback<String> c) {}
}""",
    "WebViewClient.java": """package android.webkit;
public class WebViewClient {
    public void onPageStarted(WebView v, String u, android.graphics.Bitmap f) {}
    public boolean shouldOverrideUrlLoading(WebView v, String u) { return false; }
    public void onReceivedError(WebView v, int errorCode, String desc, String failingUrl) {}
}""",
    "WebChromeClient.java": """package android.webkit;
public class WebChromeClient {
    public void onShowCustomView(android.view.View v, CustomViewCallback c) {}
    public void onHideCustomView() {}
    public interface CustomViewCallback { void onCustomViewHidden(); }
}""",
    "WebSettings.java": """package android.webkit;
public class WebSettings {
    public void setJavaScriptEnabled(boolean f) {}
    public void setDomStorageEnabled(boolean f) {}
    public void setMediaPlaybackRequiresUserGesture(boolean f) {}
    public void setAllowFileAccess(boolean f) {}
    public void setBuiltInZoomControls(boolean f) {}
    public void setSupportZoom(boolean f) {}
    public void setUseWideViewPort(boolean f) {}
    public void setLoadWithOverviewMode(boolean f) {}
}""",
    "ValueCallback.java": """package android.webkit;
public class ValueCallback<T> { public void onReceiveValue(T v) {} }""",
    "Bitmap.java": """package android.graphics;
public class Bitmap {}""",
    "Bundle.java": """package android.os;
public class Bundle { public Bundle() {} }""",
    "Intent.java": """package android.content;
public class Intent {
    public static final String ACTION_VIEW = "android.intent.action.VIEW";
    public static final int FLAG_ACTIVITY_NEW_TASK = 0x10000000;
    public Intent() {}
    public Intent(String a) {}
    public Intent(String a, android.net.Uri u) {}
    public Intent setAction(String a) { return this; }
    public Intent setData(android.net.Uri d) { return this; }
    public Intent addFlags(int f) { return this; }
}""",
    "Uri.java": """package android.net;
public class Uri {
    public static Uri parse(String s) { return null; }
    public String getHost() { return null; }
}""",
    "Window.java": """package android.view;
public class Window {
    public void setFlags(int f, int m) {}
    public static final int FLAG_FULLSCREEN = 0x00000400;
}""",
    "WindowManager.java": """package android.view;
public class WindowManager {
    public static class LayoutParams extends ViewGroup.LayoutParams {
        public static final int FLAG_FULLSCREEN = 0x00000400;
        public LayoutParams(int w, int h) { super(w, h); }
    }
}""",
}

# ── Launcher HTML (embedded in APK, does LAN/tunnel detection) ─────────────────

def make_launcher_html():
    return textwrap.dedent(r"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>FIFA 2026</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
  min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center}
.spinner{width:36px;height:36px;border:3px solid rgba(212,168,83,.2);border-top-color:#d4a853;
  border-radius:50%;animation:spin .8s linear infinite;margin-bottom:20px}
@keyframes spin{to{transform:rotate(360deg)}}
.status{font-size:.9em;color:#888;text-align:center;line-height:1.6}
</style>
</head><body>
<div class="spinner"></div>
<div class="status" id="msg">正在连接...</div>
<script>
var PORT = 8086;
var LAN  = "192.168.0.10";
var WAN  = "sethshi.dynv6.net";
var _lan = "";
var _tunnel = "";
var _lanDone = false;
var _tunnelDone = false;

var msg = document.getElementById("msg");

// Step 1: LAN probe (2s timeout, any response = reachable)
(function(){
  try {
    var x = new XMLHttpRequest();
    x.open("HEAD", "http://" + LAN + ":" + PORT + "/", true);
    x.timeout = 2000;
    x.onload = function(){ _lan = LAN; _lanDone = true; };
    x.onerror = function(){ _lanDone = true; };
    x.ontimeout = function(){ _lanDone = true; };
    x.send();
  } catch(e){ _lanDone = true; }
})();

// Step 2: Tunnel discovery via DNS TXT (DoH)
(function(){
  function tryDoh(url){
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.timeout = 5000;
    xhr.onload = function(){
      try {
        var data = JSON.parse(xhr.responseText);
        var answers = data.Answer || data.answer || [];
        for(var i=0;i<answers.length;i++){
          var txt = answers[i].data || answers[i].value || "";
          txt = txt.replace(/"/g,"");
          if(txt && txt.indexOf("https://")===0){
            _tunnel = txt;
            break;
          }
        }
      } catch(e){}
      _tunnelDone = true;
    };
    xhr.onerror = function(){ _tunnelDone = true; };
    xhr.ontimeout = function(){ _tunnelDone = true; };
    xhr.send();
  }
  tryDoh("https://dns.alidns.com/resolve?name=tunnel.sethshi.dynv6.net&type=TXT");
  // Backup: try Cloudflare DoH after 3s if still empty
  setTimeout(function(){
    if(!_tunnel) tryDoh("https://1.1.1.1/dns-query?name=tunnel.sethshi.dynv6.net&type=TXT");
  }, 3000);
})();

// Wait for both probes, then connect (max 8s total)
function tryConnect(){
  if(_lan){
    msg.textContent = "局域网连接中...";
    location.href = "http://" + _lan + ":" + PORT + "/";
    return;
  }
  if(_tunnel){
    msg.textContent = "隧道连接中...";
    location.href = _tunnel + "/fifa/";
    return;
  }
  if(!_lanDone || !_tunnelDone){
    // Still probing, wait
    msg.textContent = "探测网络中...";
    setTimeout(tryConnect, 500);
    return;
  }
  // All probes done, no LAN and no tunnel -> fallback to WAN
  msg.textContent = "尝试直连...";
  location.href = "http://" + WAN + ":" + PORT + "/";
}

// Start connecting as soon as LAN probe returns (good or bad)
// But also set a max wait of 6s for tunnel discovery
var _connectTimer = setTimeout(tryConnect, 6000);
// If LAN responds immediately, go right away
var _lanCheck = setInterval(function(){
  if(_lan){
    clearInterval(_lanCheck);
    clearTimeout(_connectTimer);
    tryConnect();
  } else if(_lanDone && _tunnelDone){
    clearInterval(_lanCheck);
    clearTimeout(_connectTimer);
    tryConnect();
  }
}, 300);
</script>
</body></html>
""")

# ── MainActivity ───────────────────────────────────────────────────────────────

MAIN_ACTIVITY = textwrap.dedent(r"""
package com.seth.fifa26;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.graphics.Bitmap;
import android.net.Uri;

public class MainActivity extends Activity {

    private FrameLayout root;
    private WebView webView;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private boolean _backOnce = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().setFlags(
            WindowManager.LayoutParams.FLAG_FULLSCREEN,
            WindowManager.LayoutParams.FLAG_FULLSCREEN
        );

        root = new FrameLayout(this);
        setContentView(root);

        webView = new WebView(this);
        root.addView(webView, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));

        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setAllowFileAccess(true);
        ws.setBuiltInZoomControls(false);
        ws.setSupportZoom(false);
        ws.setUseWideViewPort(true);
        ws.setLoadWithOverviewMode(true);

        webView.setWebViewClient(new FIFAWVC());
        webView.setWebChromeClient(new FIFAWCC(this));

        // Load local launcher page (LAN/tunnel detection)
        String html = makeLauncherHtml();
        webView.loadDataWithBaseURL("http://localhost", html, "text/html", "UTF-8", null);
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {
            if (customViewCallback != null) customViewCallback.onCustomViewHidden();
            return;
        }
        if (webView != null && webView.canGoBack()) {
            _backOnce = false;
            webView.goBack();
            return;
        }
        if (_backOnce) {
            super.onBackPressed();
            return;
        }
        _backOnce = true;
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.resumeTimers();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) webView.pauseTimers();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            root.removeView(webView);
            webView.destroy();
        }
        super.onDestroy();
    }

    void setCV(View v) { customView = v; }
    View getCV() { return customView; }
    void setCVCB(WebChromeClient.CustomViewCallback cb) { customViewCallback = cb; }
    WebChromeClient.CustomViewCallback getCVCB() { return customViewCallback; }
    FrameLayout getRoot() { return root; }
    WebView getWV() { return webView; }

    String makeLauncherHtml() {
        return "{{LAUNCHER_HTML}}";
    }
}
""")

# ── WebViewClient ──────────────────────────────────────────────────────────────

WVC_SRC = textwrap.dedent(r"""
package com.seth.fifa26;

import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.content.Intent;
import android.graphics.Bitmap;
import android.net.Uri;

public class FIFAWVC extends WebViewClient {

    private static final String LAN = "192.168.0.10";
    private static final String WAN = "sethshi.dynv6.net";
    private static final int PORT = 8086;

    @Override
    public void onPageStarted(WebView view, String url, Bitmap favicon) {
        // Inject APK flag for web page detection
        view.evaluateJavascript("window.__FIFA_APK=true;", null);
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, String url) {
        if (url == null) return false;

        // launch:// protocol: navigate to our server
        if (url.startsWith("launch://")) {
            String rest = url.substring(9);
            view.loadUrl("http://" + rest);
            return true;
        }

        // External links -> open in browser
        if (url.startsWith("http://") || url.startsWith("https://")) {
            String host = Uri.parse(url).getHost();
            if (host != null) host = host.replaceAll("[\\[\\]]", "");
            boolean isOurSite = host != null && (
                host.equals(LAN) ||
                host.equals(WAN) ||
                host.startsWith("240e:") ||
                host.endsWith(".trycloudflare.com")
            );
            if (!isOurSite) {
                try {
                    Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    view.getContext().startActivity(intent);
                } catch (Exception e) {}
                return true;
            }
        }
        return false;
    }

    @Override
    public void onReceivedError(WebView view, int errorCode, String desc, String failingUrl) {
        // Only handle errors for our server URLs, not for the launcher page
        if (failingUrl == null || failingUrl.startsWith("data:") ||
            failingUrl.startsWith("http://localhost")) {
            return;
        }
        // Show error page with retry
        String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            + "<style>*{margin:0;padding:0;box-sizing:border-box}"
            + "body{background:#0a0a0f;color:#e0e0e0;font-family:sans-serif;"
            + "display:flex;flex-direction:column;align-items:center;justify-content:center;"
            + "min-height:100vh;text-align:center;padding:40px 20px}"
            + ".icon{font-size:64px;margin-bottom:20px}"
            + "h2{font-size:1.3em;color:#d4a853;margin-bottom:12px}"
            + "p{color:#888;font-size:.9em;margin-bottom:24px;line-height:1.6}"
            + "button{background:#d4a853;color:#000;border:none;border-radius:24px;"
            + "padding:12px 32px;font-size:1em;font-weight:700;cursor:pointer;margin:8px}"
            + "button:active{opacity:.8}</style></head>"
            + "<body><div class='icon'>&#9917;</div>"
            + "<h2>无法连接服务器</h2>"
            + "<p>请检查网络连接<br>确保与服务器在同一局域网或外网可达</p>"
            + "<button onclick=\"location.href='http://localhost'\">重新探测</button>"
            + "<button onclick=\"location.href='http://" + LAN + ":" + PORT + "/'\">局域网直连</button>"
            + "</body></html>";
        view.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
    }
}
""")

# ── WebChromeClient (fullscreen video support) ─────────────────────────────────

WCC_SRC = textwrap.dedent(r"""
package com.seth.fifa26;

import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.webkit.WebChromeClient;
import android.webkit.WebView;

public class FIFAWCC extends WebChromeClient {
    private MainActivity act;

    public FIFAWCC(MainActivity a) { act = a; }

    @Override
    public void onShowCustomView(View view, CustomViewCallback callback) {
        if (act.getCV() != null) {
            callback.onCustomViewHidden();
            return;
        }
        act.setCV(view);
        act.setCVCB(callback);
        act.getRoot().removeView(act.getWV());
        act.getRoot().addView(view, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        act.setRequestedOrientation(0); // landscape for video
    }

    @Override
    public void onHideCustomView() {
        if (act.getCV() == null) return;
        act.getRoot().removeView(act.getCV());
        act.getRoot().addView(act.getWV(), new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        act.setCV(null);
        act.setCVCB(null);
        act.setRequestedOrientation(1); // back to portrait
    }
}
""")

# ── Manifest ───────────────────────────────────────────────────────────────────

MANIFEST = textwrap.dedent("""\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.seth.fifa26"
    android:versionCode="3"
    android:versionName="2.0">

    <uses-permission android:name="android.permission.INTERNET"/>

    <uses-sdk android:minSdkVersion="19" android:targetSdkVersion="28"/>

    <uses-feature android:name="android.hardware.touchscreen" android:required="false"/>

    <application
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:usesCleartextTraffic="true"
        android:networkSecurityConfig="@xml/network_security_config"
        android:hardwareAccelerated="true"
        android:theme="@android:style/Theme.NoTitleBar.Fullscreen">

        <activity
            android:name=".MainActivity"
            android:configChanges="orientation|screenSize|keyboardHidden"
            android:screenOrientation="portrait"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
    </application>
</manifest>
""")

# ── Resources ──────────────────────────────────────────────────────────────────

STRINGS_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">FIFA</string>
</resources>
"""

NETWORK_SECURITY_XML = """<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true"/>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">sethshi.dynv6.net</domain>
        <domain includeSubdomains="true">192.168.0.10</domain>
        <domain includeSubdomains="true">trycloudflare.com</domain>
    </domain-config>
</network-security-config>
"""

# ── Build pipeline ─────────────────────────────────────────────────────────────

def run(cmd, desc=""):
    print(f"  > {desc or ' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        err = r.stderr
        if isinstance(err, bytes):
            try: err = err.decode("utf-8")
            except: err = err.decode("gbk", errors="replace")
        print(f"  ERROR: {err[:500]}")
        sys.exit(1)
    out = r.stdout
    if isinstance(out, bytes):
        try: out = out.decode("utf-8")
        except: out = out.decode("gbk", errors="replace")
    return out


def generate_icon(src_path, dst_path, size=192):
    """Generate square icon from WC26_Logo.png using PIL."""
    try:
        from PIL import Image
    except ImportError:
        print("  WARNING: PIL not found, copying logo as-is")
        shutil.copy2(src_path, dst_path)
        return
    img = Image.open(src_path).convert("RGBA")
    w, h = img.size
    canvas_size = max(w, h)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    offset_x = (canvas_size - w) // 2
    offset_y = (canvas_size - h) // 2
    canvas.paste(img, (offset_x, offset_y), img)
    canvas = canvas.resize((size, size), Image.LANCZOS)
    canvas.save(dst_path, "PNG")
    print(f"  Icon: {dst_path} ({size}x{size})")


def build_apk():
    pkg = "com.seth.fifa26"
    label = "FIFA"

    print(f"\n{'='*60}")
    print(f"  Building fifa.apk  ({pkg})  v2.0")
    print(f"{'='*60}")

    tmpdir = tempfile.mkdtemp(prefix="fifa_apk_")
    print(f"  WorkDir: {tmpdir}")

    try:
        mkdir = lambda p: os.makedirs(p, exist_ok=True)
        gen_dir   = os.path.join(tmpdir, "gen")
        obj_dir   = os.path.join(tmpdir, "obj")
        res_dir   = os.path.join(tmpdir, "res")
        mipmap    = os.path.join(res_dir, "mipmap-xxxhdpi")
        values    = os.path.join(res_dir, "values")
        xml_dir   = os.path.join(res_dir, "xml")
        stubs_dir = os.path.join(gen_dir, "stubs")
        act_dir   = os.path.join(gen_dir, "com", "seth", "fifa26")

        for d in [gen_dir, obj_dir, mipmap, values, xml_dir, stubs_dir, act_dir]:
            mkdir(d)

        # ── 1. Generate icon ──
        icon_dst = os.path.join(mipmap, "ic_launcher.png")
        generate_icon(LOGO_SRC, icon_dst, size=192)

        # ── 2. Write resources ──
        with open(os.path.join(values, "strings.xml"), "w", encoding="utf-8") as f:
            f.write(STRINGS_XML)
        with open(os.path.join(xml_dir, "network_security_config.xml"), "w", encoding="utf-8") as f:
            f.write(NETWORK_SECURITY_XML)

        # ── 3. Write manifest ──
        manifest_path = os.path.join(tmpdir, "AndroidManifest.xml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(MANIFEST)

        # ── 4. Compile resources ──
        compiled_res = os.path.join(tmpdir, "resources.zip")
        run([AAPT2, "compile", "--dir", res_dir, "-o", compiled_res], "aapt2 compile res")

        # ── 5. Link resources ──
        apk_base = os.path.join(tmpdir, "base.apk")
        run([
            AAPT2, "link",
            "-o", apk_base,
            "--manifest", manifest_path,
            "-I", FRAMEWORK,
            "--java", gen_dir,
            "--auto-add-overlay",
            compiled_res
        ], "aapt2 link")

        # ── 6. Write stub Java files ──
        for fname, content in STUB_FILES.items():
            with open(os.path.join(stubs_dir, fname), "w", encoding="utf-8") as f:
                f.write(content)

        # ── 7. Generate app Java sources ──
        launcher_html = make_launcher_html()
        launcher_escaped = launcher_html.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")

        main_java = MAIN_ACTIVITY.replace("{{LAUNCHER_HTML}}", launcher_escaped)
        with open(os.path.join(act_dir, "MainActivity.java"), "w", encoding="utf-8") as f:
            f.write(main_java)

        with open(os.path.join(act_dir, "FIFAWVC.java"), "w", encoding="utf-8") as f:
            f.write(WVC_SRC)

        with open(os.path.join(act_dir, "FIFAWCC.java"), "w", encoding="utf-8") as f:
            f.write(WCC_SRC)

        # ── 8. Compile Java -> .class ──
        java_files = []
        for root_dir, dirs, files in os.walk(stubs_dir):
            for fn in files:
                if fn.endswith(".java"):
                    java_files.append(os.path.join(root_dir, fn))
        java_files.append(os.path.join(act_dir, "MainActivity.java"))
        java_files.append(os.path.join(act_dir, "FIFAWVC.java"))
        java_files.append(os.path.join(act_dir, "FIFAWCC.java"))

        # Find R.java
        for root_dir, dirs, files in os.walk(gen_dir):
            for fn in files:
                if fn == "R.java":
                    java_files.append(os.path.join(root_dir, fn))

        javac_cmd = [
            os.path.join(r"G:\AI\.temp\jdk8", "bin", "javac.exe"),
            "-source", "1.8", "-target", "1.8",
            "-encoding", "UTF-8",
            "-cp", FRAMEWORK,
            "-d", obj_dir,
            "-sourcepath", gen_dir,
        ] + java_files

        run(javac_cmd, f"javac ({len(java_files)} files)")

        # ── 9. .class -> .dex ──
        class_files = []
        for root_dir, dirs, files in os.walk(obj_dir):
            for fn in files:
                if fn.endswith(".class"):
                    class_files.append(os.path.join(root_dir, fn))

        classlist = os.path.join(tmpdir, "classlist.txt")
        with open(classlist, "w") as f:
            for cf in class_files:
                f.write(cf + "\n")

        run([
            JAVA, "-cp", D8_JAR,
            "com.android.tools.r8.D8",
            "--lib", FRAMEWORK,
            "--output", tmpdir,
            "--min-api", "19",
            "@" + classlist
        ], "d8 (dex)")

        # ── 10. Package APK ──
        unsigned_apk = os.path.join(tmpdir, "unsigned.apk")
        shutil.copy2(apk_base, unsigned_apk)

        import zipfile
        dex_path = os.path.join(tmpdir, "classes.dex")
        with zipfile.ZipFile(unsigned_apk, 'a') as zf:
            zf.write(dex_path, "classes.dex")
        print("  > add classes.dex to APK")

        # ── 11. Sign APK ──
        signed_apk = os.path.join(tmpdir, "signed.apk")
        run([
            os.path.join(r"G:\AI\.temp\jdk8", "bin", "jarsigner.exe"),
            "-sigalg", "SHA256withRSA",
            "-digestalg", "SHA-256",
            "-keystore", KEYSTORE,
            "-storepass", KEY_PASS,
            "-keypass", KEY_PASS,
            "-signedjar", signed_apk,
            unsigned_apk,
            KEY_ALIAS
        ], "jarsigner")

        # ── 12. Copy output ──
        os.makedirs(os.path.dirname(OUT_APK), exist_ok=True)
        shutil.copy2(signed_apk, OUT_APK)
        size_kb = os.path.getsize(OUT_APK) / 1024
        print(f"\n  [OK] fifa.apk -> {OUT_APK} ({size_kb:.1f} KB)")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Preflight checks
    for name, path in [("aapt2", AAPT2), ("d8.jar", D8_JAR),
                       ("framework", FRAMEWORK), ("keystore", KEYSTORE)]:
        if not os.path.exists(path):
            print(f"ERROR: {name} not found at {path}")
            sys.exit(1)

    if not os.path.exists(JAVA):
        print(f"ERROR: java not found at {JAVA}")
        sys.exit(1)

    if not os.path.exists(LOGO_SRC):
        print(f"ERROR: logo not found at {LOGO_SRC}")
        sys.exit(1)

    print("Build environment OK")
    print(f"  aapt2:   {AAPT2}")
    print(f"  d8:      {D8_JAR}")
    print(f"  fw:      {FRAMEWORK}")
    print(f"  key:     {KEYSTORE}")
    print(f"  logo:    {LOGO_SRC}")
    print(f"  output:  {OUT_APK}")

    build_apk()
    print("\nDone!")
