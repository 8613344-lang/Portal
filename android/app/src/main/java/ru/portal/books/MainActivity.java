package ru.portal.books;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.text.InputType;

/** Single-purpose client. Credentials stay in the platform's first-party session. */
public class MainActivity extends Activity {
    private WebView web;
    private String origin;
    private ProgressBar progress;
    private boolean connectionErrorShown;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        origin = getPreferences(MODE_PRIVATE).getString("server", "");
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(250,247,240));
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        root.addView(progress, new FrameLayout.LayoutParams(-1, 6));
        setContentView(root);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        root.requestApplyInsets();
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                progress.setVisibility(value == 100 ? View.GONE : View.VISIBLE);
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!sameServer(uri)) return true;
                String path = uri.getPath();
                boolean book = path != null && (path.startsWith("/books/") || path.startsWith("/read/"));
                boolean allowed = "/app/".equals(path) || "/app/login/".equals(path) || "/app/logout/".equals(path) || book;
                if (!allowed || (path != null && path.contains("/download/"))) return true;
                if (book && !"1".equals(uri.getQueryParameter("app"))) { view.loadUrl(withMode(uri.toString(), true)); return true; }
                return false;
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showConnectionError();
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 400) showConnectionError();
            }
            @Override public void onPageFinished(WebView view, String url) { CookieManager.getInstance().flush(); }
        });
        if (origin.isEmpty()) configureServer();
        else if (state == null || web.restoreState(state) == null) openShelf();
    }

    private String device() { return getResources().getConfiguration().smallestScreenWidthDp >= 600 ? "tablet" : "phone"; }

    private boolean sameServer(Uri uri) {
        Uri base = Uri.parse(origin);
        return base.getScheme() != null && base.getScheme().equals(uri.getScheme())
            && base.getHost() != null && base.getHost().equalsIgnoreCase(uri.getHost()) && base.getPort() == uri.getPort();
    }

    private String withMode(String url, boolean book) {
        Uri uri = Uri.parse(url);
        Uri.Builder builder = uri.buildUpon().clearQuery();
        for (String key : uri.getQueryParameterNames()) {
            if (!"device".equals(key) && !"app".equals(key)) for (String value : uri.getQueryParameters(key)) builder.appendQueryParameter(key, value);
        }
        builder.appendQueryParameter("device", device());
        if (book) builder.appendQueryParameter("app", "1");
        return builder.build().toString();
    }

    private void openShelf() { web.loadUrl(origin + "/app/?device=" + device()); }

    private void configureServer() {
        EditText field = new EditText(this);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        field.setSingleLine(true);
        field.setHint("https://books.example.ru");
        field.setText(origin);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Адрес платформы книг")
            .setMessage("Введите адрес вашего сервера. Он сохранится для следующих запусков.")
            .setView(field).setPositiveButton("Открыть", null).setNegativeButton("Закрыть", (d,w) -> finish()).create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            Uri uri = Uri.parse(field.getText().toString().trim());
            boolean validScheme = "https".equals(uri.getScheme()) || (BuildConfig.DEBUG && "http".equals(uri.getScheme()));
            if (!validScheme || uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null || (uri.getPath() != null && !uri.getPath().isEmpty() && !"/".equals(uri.getPath()))) {
                field.setError("Укажите адрес сервера HTTPS без пути и пароля"); return;
            }
            boolean changed = !origin.equals(uri.buildUpon().path(null).build().toString());
            if (changed) {
                web.clearHistory(); web.clearCache(true);
            }
            origin = uri.buildUpon().path(null).build().toString();
            getPreferences(MODE_PRIVATE).edit().putString("server", origin).apply();
            dialog.dismiss();
            if (changed) CookieManager.getInstance().removeAllCookies(value -> { CookieManager.getInstance().flush(); if (!isFinishing()) openShelf(); });
            else openShelf();
        }));
        dialog.show();
    }

    private void showConnectionError() {
        if (connectionErrorShown || isFinishing()) return;
        connectionErrorShown = true;
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Не удалось открыть книги")
            .setMessage("Проверьте интернет и доступность сервера.")
            .setPositiveButton("Повторить", (d,w) -> openShelf())
            .setNeutralButton("Адрес сервера", (d,w) -> configureServer())
            .setNegativeButton("Закрыть", (d,w) -> finish()).create();
        dialog.setOnDismissListener(d -> connectionErrorShown = false);
        dialog.show();
    }

    @Override public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        String url = web.getUrl();
        if (url != null && sameServer(Uri.parse(url))) web.loadUrl(withMode(url, url.contains("/books/") || url.contains("/read/")));
    }
    @Override public void onBackPressed() {
        String url = web.getUrl();
        if (url != null && (url.contains("/books/") || url.contains("/read/") || url.contains("/app/login/"))) openShelf();
        else super.onBackPressed();
    }
    @Override protected void onSaveInstanceState(Bundle state) { web.saveState(state); super.onSaveInstanceState(state); }
    @Override protected void onPause() { web.evaluateJavascript("window.dispatchEvent(new Event('pagehide'));document.querySelectorAll('audio').forEach(a=>a.pause());", null); web.onPause(); super.onPause(); }
    @Override protected void onResume() { super.onResume(); if (web != null) web.onResume(); }
    @Override protected void onDestroy() { web.destroy(); super.onDestroy(); }
}
