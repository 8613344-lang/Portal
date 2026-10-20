package ru.portal.books;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.IOException;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.DateFormat;
import java.util.Date;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Book-only client. The native recovery screen does not depend on a working WebView. */
public class MainActivity extends Activity {
    private static final String ORIGIN = "https://qicko.ru";
    private static final String TAG = "PortalBooks";
    private FrameLayout root;
    private WebView web;
    private ProgressBar progress;
    private TextView loading;
    private LinearLayout recovery;
    private TextView recoveryTitle;
    private TextView recoveryMessage;
    private boolean mainFrameFailed;
    private LinearLayout nativeMenu;
    private Button saveOffline;
    private View offlineScreen;
    private OfflinePdfView offlineReader;
    private OfflineBooks offlineBooks;
    private final ExecutorService downloads = Executors.newSingleThreadExecutor();
    private volatile int accountGeneration;
    private String account = "guest";
    private String accountLabel = "Гость";
    private String currentPdf;
    private String currentTitle;
    private boolean downloading;
    private boolean resumed;
    private String currentVideo;
    private Button videoButton;
    private String pendingVideo, pendingVideoCookies;
    private boolean contextLoading;
    private boolean saveRequestedAfterContext;
    private View customVideo;
    private WebChromeClient.CustomViewCallback customVideoCallback;
    private static final int SAVE_VIDEO = 1001;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        offlineBooks = new OfflineBooks(this);
        account = getPreferences(MODE_PRIVATE).getString("offline_account", "guest");
        accountLabel = getPreferences(MODE_PRIVATE).getString("offline_label", "Гость");
        createLayout();
        if (!createWebView()) return;
        boolean restored = false;
        if (state != null && ORIGIN.equals(state.getString("portal_origin"))) {
            try {
                restored = web.restoreState(state) != null && allowedPage(Uri.parse(web.getUrl() == null ? "" : web.getUrl()));
            } catch (RuntimeException error) {
                Log.w(TAG, "Could not restore WebView state", error);
            }
        }
        if (restored) web.reload();
        else openShelf();
    }

    private void createLayout() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(251,247,238));
        loading = new TextView(this);
        loading.setText("Открываем ваши книги…");
        loading.setTextSize(18);
        loading.setTextColor(Color.rgb(98,109,109));
        loading.setGravity(Gravity.CENTER);
        loading.setBackgroundColor(Color.rgb(251,247,238));
        root.addView(loading, contentParams());
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        root.addView(progress, new FrameLayout.LayoutParams(-1, dp(3)));
        recovery = new LinearLayout(this);
        recovery.setOrientation(LinearLayout.VERTICAL);
        recovery.setPadding(dp(24), dp(26), dp(24), dp(24));
        GradientDrawable background = new GradientDrawable();
        background.setColor(Color.rgb(255,253,247));
        background.setCornerRadius(dp(22));
        background.setStroke(dp(1), Color.rgb(226,219,203));
        recovery.setBackground(background);
        recoveryTitle = new TextView(this);
        recoveryTitle.setTextSize(23);
        recoveryTitle.setTextColor(Color.rgb(43,63,67));
        recovery.addView(recoveryTitle);
        recoveryMessage = new TextView(this);
        recoveryMessage.setTextSize(16);
        recoveryMessage.setTextColor(Color.rgb(98,109,109));
        recoveryMessage.setPadding(0, dp(18), 0, dp(18));
        recovery.addView(recoveryMessage);
        Button retry = new Button(this);
        retry.setText("Повторить загрузку");
        retry.setAllCaps(false);
        retry.setOnClickListener(view -> openShelf());
        recovery.addView(retry, new LinearLayout.LayoutParams(-1, -2));
        Button browser = new Button(this);
        browser.setText("Открыть книги в браузере");
        browser.setAllCaps(false);
        browser.setOnClickListener(view -> openBrowser(Uri.parse(ORIGIN + "/app/?device=" + device())));
        recovery.addView(browser, new LinearLayout.LayoutParams(-1, -2));
        FrameLayout.LayoutParams panel = new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER);
        panel.setMargins(dp(24), dp(24), dp(24), dp(24));
        root.addView(recovery, panel);
        recovery.setVisibility(View.GONE);
        nativeMenu = new LinearLayout(this);
        nativeMenu.setGravity(Gravity.CENTER_VERTICAL);
        nativeMenu.setBackgroundColor(Color.rgb(255,253,247));
        Button online = nativeButton("Книги");
        online.setOnClickListener(view -> { closeOfflineScreen(); openShelf(); });
        nativeMenu.addView(online, new LinearLayout.LayoutParams(0, -1, 1));
        saveOffline = nativeButton("Сохранить PDF");
        saveOffline.setEnabled(false);
        saveOffline.setOnClickListener(view -> confirmOfflineDownload());
        nativeMenu.addView(saveOffline, new LinearLayout.LayoutParams(0, -1, 1));
        videoButton = nativeButton("Видео");
        videoButton.setEnabled(false);
        videoButton.setOnClickListener(view -> {
            if (currentVideo != null && web != null) web.loadUrl(withMode(currentVideo, true));
        });
        nativeMenu.addView(videoButton, new LinearLayout.LayoutParams(0, -1, 1));
        Button offline = nativeButton("Без интернета");
        offline.setOnClickListener(view -> showOfflineShelf());
        nativeMenu.addView(offline, new LinearLayout.LayoutParams(0, -1, 1));
        root.addView(nativeMenu, new FrameLayout.LayoutParams(-1, dp(56), Gravity.BOTTOM));
        setContentView(root);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        root.requestApplyInsets();
    }

    private boolean createWebView() {
        try {
            WebView candidate = new WebView(this);
            web = candidate;
            WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
            WebSettings settings = candidate.getSettings();
            settings.setUseWideViewPort(true);
            settings.setLoadWithOverviewMode(false);
            settings.setTextZoom(100);
            settings.setJavaScriptEnabled(true);
            settings.setDomStorageEnabled(true);
            settings.setAllowFileAccess(false);
            settings.setAllowContentAccess(false);
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
            settings.setMediaPlaybackRequiresUserGesture(false);
            settings.setBuiltInZoomControls(true);
            settings.setDisplayZoomControls(false);
            CookieManager.getInstance().setAcceptCookie(true);
            CookieManager.getInstance().setAcceptThirdPartyCookies(candidate, false);
            candidate.setWebChromeClient(new WebChromeClient() {
                @Override public void onShowCustomView(View view, CustomViewCallback callback) {
                    if (customVideo != null) { callback.onCustomViewHidden(); return; }
                    customVideo = view; customVideoCallback = callback;
                    root.addView(view, new FrameLayout.LayoutParams(-1, -1));
                    nativeMenu.setVisibility(View.GONE);
                    updateScreenAwake();
                }
                @Override public void onHideCustomView() { closeCustomVideo(); }
                @Override public void onProgressChanged(WebView view, int value) {
                    if (view != web) return;
                    progress.setProgress(value);
                    progress.setVisibility(value == 100 || mainFrameFailed ? View.GONE : View.VISIBLE);
                }
            });
            candidate.setWebViewClient(new WebViewClient() {
                @Override public void onPageStarted(WebView view, String url, Bitmap favicon) {
                    if (view != web) return;
                    mainFrameFailed = false;
                    recovery.setVisibility(View.GONE);
                    loading.setVisibility(View.VISIBLE);
                    view.setVisibility(View.VISIBLE);
                    currentPdf = null;
                    saveRequestedAfterContext = false;
                    Uri address = Uri.parse(url);
                    currentVideo = videoAddress(address);
                    saveOffline.setEnabled(bookPage(address.getPath()) && !downloading);
                    videoButton.setEnabled(currentVideo != null);
                    updateScreenAwake();
                    if (sameServer(address) && ("/app/logout/".equals(address.getPath()) || "1".equals(address.getQueryParameter("account_deleted")))) forgetOfflineAccount();
                    nativeMenu.bringToFront();
                }
                @Override public void onPageCommitVisible(WebView view, String url) {
                    if (view == web && !mainFrameFailed) loading.setVisibility(View.GONE);
                }
                @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                    Uri uri = request.getUrl();
                    if (!sameServer(uri)) return true;
                    String path = uri.getPath();
                    if (VideoDownload.allowed(uri) && "1".equals(uri.getQueryParameter("download"))) {
                        chooseVideoFile(uri.toString()); return true;
                    }
                    if (path != null && (path.startsWith("/accounts/password-reset/") || path.startsWith("/accounts/register/"))) {
                        openBrowser(uri);
                        return true;
                    }
                    if ("/books/".equals(path)) { openShelf(); return true; }
                    if (!allowedPage(uri)) return true;
                    if (bookPage(path) && !"1".equals(uri.getQueryParameter("app"))) {
                        view.loadUrl(withMode(uri.toString(), true));
                        return true;
                    }
                    return false;
                }
                @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                    if (request.isForMainFrame() && view == web) {
                        Log.w(TAG, "Main frame network error: " + error.getErrorCode());
                        showFailure("Не удалось открыть книги", "Проверьте подключение к интернету и повторите загрузку. Сайт: qicko.ru.");
                    }
                }
                @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                    if (request.isForMainFrame() && view == web && response.getStatusCode() >= 400) {
                        Log.w(TAG, "Main frame HTTP error: " + response.getStatusCode());
                        showFailure("Книги временно недоступны", "Сервер вернул ошибку " + response.getStatusCode() + ". Повторите загрузку немного позже.");
                    }
                }
                @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                    handler.cancel();
                    if (view == web) {
                        Log.w(TAG, "HTTPS certificate rejected: " + error.getPrimaryError());
                        showFailure("Не удалось проверить соединение", "Проверьте дату и время на устройстве. Если они верны, повторите попытку позже.");
                    }
                }
                @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                    Log.w(TAG, "WebView renderer stopped; crashed=" + detail.didCrash());
                    if (view == web) {
                        destroyWebView();
                        showFailure("Просмотр книг остановлен", "Нажмите «Повторить загрузку», чтобы снова открыть вашу книжную полку.");
                    }
                    return true;
                }
                @Override public void onPageFinished(WebView view, String url) {
                    if (view != web) return;
                    CookieManager.getInstance().flush();
                    loading.setVisibility(View.GONE);
                    if (!mainFrameFailed) recovery.setVisibility(View.GONE);
                    if (!mainFrameFailed && sameServer(Uri.parse(url))) inspectAccountAndBook(view, url);
                }
            });
            root.addView(candidate, 0, contentParams());
            candidate.setDownloadListener((url, userAgent, disposition, mime, length) -> {
                if (VideoDownload.allowed(Uri.parse(url))) chooseVideoFile(url);
            });
            return true;
        } catch (RuntimeException | LinkageError error) {
            Log.e(TAG, "WebView initialization failed", error);
            destroyWebView();
            showFailure("Не удалось запустить просмотр", "Обновите Android System WebView или Google Chrome на устройстве и повторите попытку. Книги также можно открыть в браузере.");
            return false;
        }
    }

    private void showFailure(String title, String message) {
        if (isFinishing() || isDestroyed()) return;
        mainFrameFailed = true;
        updateScreenAwake();
        progress.setVisibility(View.GONE);
        loading.setVisibility(View.GONE);
        if (web != null) web.setVisibility(View.INVISIBLE);
        recoveryTitle.setText(title);
        recoveryMessage.setText(message);
        recovery.setVisibility(View.VISIBLE);
        recovery.bringToFront();
        if (offlineScreen != null) offlineScreen.bringToFront();
        nativeMenu.bringToFront();
    }

    private void destroyWebView() {
        closeCustomVideo();
        WebView previous = web;
        web = null;
        if (previous == null) return;
        if (previous.getParent() instanceof ViewGroup) ((ViewGroup) previous.getParent()).removeView(previous);
        try { previous.destroy(); }
        catch (RuntimeException | LinkageError error) { Log.w(TAG, "WebView cleanup failed", error); }
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private FrameLayout.LayoutParams contentParams() {
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(-1, -1);
        params.bottomMargin = dp(56);
        return params;
    }
    private Button nativeButton(String title) {
        Button button = new Button(this);
        button.setText(title);
        button.setAllCaps(false);
        button.setTextSize(12);
        button.setPadding(dp(3), 0, dp(3), 0);
        return button;
    }
    private String device() { return getResources().getConfiguration().smallestScreenWidthDp >= 600 ? "tablet" : "phone"; }

    private boolean sameServer(Uri uri) {
        return "https".equalsIgnoreCase(uri.getScheme()) && "qicko.ru".equalsIgnoreCase(uri.getHost())
            && uri.getUserInfo() == null && (uri.getPort() == -1 || uri.getPort() == 443);
    }

    private boolean bookPage(String path) {
        return path != null && ((path.startsWith("/books/") && !"/books/".equals(path)) || path.startsWith("/read/"));
    }

    private String videoAddress(Uri uri) {
        if (!sameServer(uri) || uri.getPath() == null) return null;
        String[] parts = uri.getPath().split("/");
        if (parts.length >= 3 && "books".equals(parts[1]) && parts[2].matches("[A-Za-z0-9_-]+"))
            return ORIGIN + "/books/" + parts[2] + "/video/";
        return null;
    }

    private boolean allowedPage(Uri uri) {
        String path = uri.getPath();
        return sameServer(uri) && path != null && !path.contains("/download/")
            && ("/app/".equals(path) || "/app/login/".equals(path) || "/app/logout/".equals(path)
                || "/privacy/".equals(path) || "/app/account/delete/".equals(path) || bookPage(path));
    }

    private String withMode(String url, boolean book) {
        Uri uri = Uri.parse(url);
        Uri.Builder builder = uri.buildUpon().clearQuery();
        for (String key : uri.getQueryParameterNames()) {
            if (!"device".equals(key) && !"app".equals(key) && !"shelf".equals(key)) {
                for (String value : uri.getQueryParameters(key)) builder.appendQueryParameter(key, value);
            }
        }
        builder.appendQueryParameter("device", device());
        if (book) builder.appendQueryParameter("app", "1");
        return builder.build().toString();
    }

    private void openShelf() {
        if (web == null && !createWebView()) return;
        mainFrameFailed = false;
        recovery.setVisibility(View.GONE);
        loading.setVisibility(View.VISIBLE);
        web.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(ORIGIN + "/app/?device=" + device());
    }

    private void openBrowser(Uri uri) {
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
        catch (ActivityNotFoundException error) { Toast.makeText(this, "На устройстве не найден браузер.", Toast.LENGTH_LONG).show(); }
    }

    private void inspectAccountAndBook(WebView source, String pageUrl) {
        String script = "(()=>{const r=document.querySelector('[data-book-reader],[data-book-video]');const m=document.querySelector('meta[name=portal-account]');const l=document.querySelector('meta[name=portal-account-label]');const a=document.querySelector('a[href*=\"/download/pdf/\"]');const t=document.querySelector('.reader-top>span');return JSON.stringify({account:m?m.content:(r?r.getAttribute('data-account'):null),label:l?l.content:'',pdf:r?r.getAttribute('data-pdf-url'):(a?a.href:null),title:t?t.textContent:document.title})})()";
        source.evaluateJavascript(script, value -> {
            if (isDestroyed() || source != web || !sameBookDocument(pageUrl, source.getUrl())) return;
            try {
                Object result = new JSONTokener(value).nextValue();
                if (!(result instanceof String)) return;
                JSONObject data = new JSONObject((String) result);
                String detected = data.optString("account", "");
                if (!detected.matches("guest|[0-9]{1,20}")) { recoverBookContext(source, pageUrl); return; }
                String label = data.optString("label", "Гость");
                if (label.length() > 150) label = label.substring(0, 150);
                updateOfflineAccount(detected, label);
                String pdf = data.optString("pdf", "");
                if (pdf.startsWith("/")) pdf = ORIGIN + pdf;
                currentPdf = OfflineBooks.pdfAddress(Uri.parse(pdf)) ? pdf : null;
                if (currentPdf == null && currentVideo != null) currentPdf = currentVideo.replace("/video/", "/download/pdf/");
                if (currentPdf != null) currentVideo = currentPdf.replace("/download/pdf/", "/video/");
                videoButton.setEnabled(currentVideo != null);
                currentTitle = data.optString("title", "Книга").trim();
                if (currentTitle.length() > 200) currentTitle = currentTitle.substring(0, 200);
                saveOffline.setEnabled(bookPage(Uri.parse(pageUrl).getPath()) && !downloading && offlineScreen == null);
                updateScreenAwake();
            } catch (Exception error) { Log.w(TAG, "Could not inspect book metadata", error); }
        });
    }

    private void updateOfflineAccount(String detected, String label) {
        if (!account.equals(detected)) {
            accountGeneration++;
            account = detected;
            downloads.execute(() -> { try { offlineBooks.clear(); } catch (IOException error) { Log.w(TAG, "Could not remove previous account downloads", error); } });
            closeOfflineScreen();
        }
        accountLabel = label.isEmpty() ? ("guest".equals(account) ? "Гость" : "Ваш аккаунт") : label;
        getPreferences(MODE_PRIVATE).edit().putString("offline_account", account).putString("offline_label", accountLabel).apply();
    }

    private void forgetOfflineAccount() {
        accountGeneration++;
        account = "guest";
        accountLabel = "Гость";
        currentPdf = null;
        getPreferences(MODE_PRIVATE).edit().remove("offline_account").remove("offline_label").apply();
        closeOfflineScreen();
        downloads.execute(() -> { try { offlineBooks.clear(); } catch (IOException error) { Log.w(TAG, "Could not remove signed-out downloads", error); } });
    }

    private void confirmOfflineDownload() {
        if (downloading) return;
        if (currentPdf == null) {
            saveRequestedAfterContext = true;
            if (web != null) recoverBookContext(web, web.getUrl());
            Toast.makeText(this, "Проверяем доступ к файлу книги…", Toast.LENGTH_LONG).show();
            return;
        }
        new AlertDialog.Builder(this).setTitle("Сохранить книгу без интернета?")
            .setMessage("Сохраним PDF «" + currentTitle + "» во внутренней памяти приложения. Озвучка доступна онлайн. Копия останется до удаления книги или выхода из аккаунта; отзыв доступа на сайте не удаляет уже сохранённый PDF.")
            .setNegativeButton("Отмена", null).setPositiveButton("Сохранить", (dialog, which) -> downloadCurrentBook()).show();
    }

    private void downloadCurrentBook() {
        if (currentPdf == null || downloading || web == null) return;
        final String address = currentPdf;
        final String title = currentTitle;
        final String owner = account;
        final int token = accountGeneration;
        final String cookies = CookieManager.getInstance().getCookie(ORIGIN + "/");
        downloading = true;
        saveOffline.setEnabled(false);
        saveOffline.setText("Сохраняем…");
        downloads.execute(() -> {
            String message;
            try {
                offlineBooks.download(address, title, owner, cookies, () -> token == accountGeneration && !Thread.currentThread().isInterrupted());
                message = "Книга сохранена. Откройте «Без интернета».";
            } catch (IOException | RuntimeException error) { message = error.getMessage() == null ? "Не удалось сохранить PDF." : error.getMessage(); }
            final String result = message;
            runOnUiThread(() -> {
                if (isDestroyed()) return;
                downloading = false;
                saveOffline.setText("Сохранить PDF");
                saveOffline.setEnabled(currentPdf != null && offlineScreen == null);
                Toast.makeText(this, result, Toast.LENGTH_LONG).show();
            });
        });
    }

    private void pauseWebAudio() {
        if (web == null) return;
        try { web.evaluateJavascript("window.dispatchEvent(new Event('pagehide'));document.querySelectorAll('audio').forEach(a=>a.pause());", null); }
        catch (RuntimeException error) { Log.w(TAG, "Could not pause book audio", error); }
    }

    private void showOfflineShelf() {
        pauseWebAudio();
        closeOfflineScreen();
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(251,247,238));
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(18), dp(20), dp(18), dp(20));
        TextView heading = new TextView(this);
        heading.setText("Книги без интернета");
        heading.setTextSize(24);
        heading.setTextColor(Color.rgb(43,63,67));
        content.addView(heading);
        TextView note = new TextView(this);
        note.setText("" + accountLabel + "\nPDF без озвучки. Сохранённые копии остаются на этом устройстве до удаления или выхода из аккаунта.");
        note.setTextSize(15);
        note.setTextColor(Color.rgb(98,109,109));
        note.setPadding(0, dp(12), 0, dp(18));
        content.addView(note);
        TextView status = new TextView(this);
        status.setText("Загружаем список…");
        content.addView(status);
        scroll.addView(content);
        offlineScreen = scroll;
        updateScreenAwake();
        root.addView(scroll, contentParams());
        nativeMenu.bringToFront();
        saveOffline.setEnabled(false);
        final String owner = account;
        downloads.execute(() -> {
            try {
                List<OfflineBooks.Book> books = offlineBooks.list(owner);
                runOnUiThread(() -> {
                    if (isDestroyed() || offlineScreen != scroll || !account.equals(owner)) return;
                    status.setText(books.isEmpty() ? "Пока нет сохранённых книг. Откройте книгу онлайн и нажмите «Сохранить PDF»." : "Сохранено книг: " + books.size());
                    for (OfflineBooks.Book book : books) {
                        Button open = nativeButton(book.title + "\n" + DateFormat.getDateInstance().format(new Date(book.savedAt)));
                        open.setTextSize(17);
                        open.setOnClickListener(view -> openOfflineBook(book));
                        content.addView(open, new LinearLayout.LayoutParams(-1, -2));
                        Button remove = nativeButton("Удалить с устройства");
                        remove.setOnClickListener(view -> new AlertDialog.Builder(this).setTitle("Удалить «" + book.title + "»?")
                            .setMessage("Книга исчезнет из офлайн-полки на этом устройстве. Онлайн-книга останется на сайте.")
                            .setNegativeButton("Отмена", null).setPositiveButton("Удалить", (dialog, which) -> downloads.execute(() -> {
                                try { offlineBooks.delete(book); runOnUiThread(() -> { if (!isDestroyed()) showOfflineShelf(); }); }
                                catch (IOException error) { runOnUiThread(() -> { if (!isDestroyed()) Toast.makeText(this, "Не удалось удалить книгу.", Toast.LENGTH_LONG).show(); }); }
                            })).show());
                        content.addView(remove, new LinearLayout.LayoutParams(-1, -2));
                    }
                });
            } catch (IOException error) { runOnUiThread(() -> { if (!isDestroyed() && offlineScreen == scroll) status.setText("Не удалось прочитать список книг."); }); }
        });
    }

    private void openOfflineBook(OfflineBooks.Book book) {
        if (!book.owner.equals(account)) return;
        closeOfflineScreen();
        offlineReader = new OfflinePdfView(this, offlineBooks, book, this::showOfflineShelf);
        offlineScreen = offlineReader;
        updateScreenAwake();
        root.addView(offlineScreen, contentParams());
        nativeMenu.bringToFront();
        saveOffline.setEnabled(false);
    }

    private void closeOfflineScreen() {
        if (offlineReader != null) { offlineReader.close(); offlineReader = null; }
        if (offlineScreen != null) { root.removeView(offlineScreen); offlineScreen = null; }
        if (saveOffline != null) saveOffline.setEnabled(web != null && bookPage(Uri.parse(web.getUrl() == null ? "" : web.getUrl()).getPath()) && !downloading);
        updateScreenAwake();
    }

    private void recoverBookContext(WebView source, String pageUrl) {
        if (contextLoading || pageUrl == null || !sameServer(Uri.parse(pageUrl))) return;
        final String address = videoAddress(Uri.parse(pageUrl));
        if (address == null) return;
        final String cookies = CookieManager.getInstance().getCookie(ORIGIN + "/");
        contextLoading = true;
        downloads.execute(() -> {
            JSONObject metadata = null;
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(ORIGIN + "/app/context/").openConnection();
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(10000);
                if (cookies != null) connection.setRequestProperty("Cookie", cookies);
                if (connection.getResponseCode() != 200) throw new IOException("Context unavailable");
                try (InputStream input = connection.getInputStream(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                    byte[] buffer = new byte[1024]; int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (bytes.size() + count > 16384) throw new IOException("Context too large");
                        bytes.write(buffer, 0, count);
                    }
                    metadata = new JSONObject(bytes.toString("UTF-8"));
                }
            } catch (Exception ignored) { }
            finally { if (connection != null) connection.disconnect(); }
            final JSONObject result = metadata;
            runOnUiThread(() -> {
                contextLoading = false;
                if (isDestroyed() || source != web || !sameBookDocument(pageUrl, source.getUrl())) return;
                if (result == null) {
                    if (saveRequestedAfterContext) Toast.makeText(this, "Сохранение недоступно. Повторите после загрузки книги или обратитесь к владельцу сайта.", Toast.LENGTH_LONG).show();
                    saveRequestedAfterContext = false;
                    return;
                }
                String owner = result.optString("account", "");
                if (!owner.matches("guest|[0-9]{1,20}")) return;
                updateOfflineAccount(owner, result.optString("label", "Ваш аккаунт"));
                currentPdf = address.replace("/video/", "/download/pdf/");
                currentVideo = address;
                currentTitle = source.getTitle() == null ? "Книга" : source.getTitle();
                saveOffline.setEnabled(!downloading && offlineScreen == null);
                videoButton.setEnabled(true);
                if (saveRequestedAfterContext) { saveRequestedAfterContext = false; confirmOfflineDownload(); }
            });
        });
    }

    private boolean sameBookDocument(String first, String second) {
        if (first == null || second == null) return false;
        Uri a = Uri.parse(first), b = Uri.parse(second);
        return sameServer(a) && sameServer(b) && a.getPath() != null && a.getPath().equals(b.getPath());
    }

    private void updateScreenAwake() {
        String url = web == null ? null : web.getUrl();
        boolean reading = customVideo != null || (offlineScreen != null ? offlineReader != null :
            !mainFrameFailed && url != null && bookPage(Uri.parse(url).getPath()));
        if (resumed && reading) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private void closeCustomVideo() {
        if (customVideo == null) return;
        root.removeView(customVideo);
        customVideo = null;
        nativeMenu.setVisibility(View.VISIBLE);
        WebChromeClient.CustomViewCallback callback = customVideoCallback;
        customVideoCallback = null;
        if (callback != null) callback.onCustomViewHidden();
        updateScreenAwake();
    }

    private void chooseVideoFile(String url) {
        if (!VideoDownload.allowed(Uri.parse(url)) || pendingVideo != null) return;
        pendingVideo = url;
        pendingVideoCookies = CookieManager.getInstance().getCookie(ORIGIN + "/");
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("video/mp4");
        intent.putExtra(Intent.EXTRA_TITLE, Uri.parse(url).getPath().split("/")[2] + ".mp4");
        try { startActivityForResult(intent, SAVE_VIDEO); }
        catch (ActivityNotFoundException error) {
            pendingVideo = null; pendingVideoCookies = null;
            Toast.makeText(this, "На устройстве недоступен выбор папки. Скачайте видео через сайт.", Toast.LENGTH_LONG).show();
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != SAVE_VIDEO) return;
        final String url = pendingVideo, cookies = pendingVideoCookies;
        pendingVideo = null; pendingVideoCookies = null;
        if (result != RESULT_OK || data == null || data.getData() == null || url == null ||
                !"content".equalsIgnoreCase(data.getData().getScheme())) return;
        final Uri destination = data.getData();
        Toast.makeText(this, "Сохраняем видео в выбранный файл…", Toast.LENGTH_LONG).show();
        downloads.execute(() -> {
            String message;
            try { VideoDownload.save(this, destination, url, cookies); message = "Видео сохранено."; }
            catch (IOException | RuntimeException error) { message = "Не удалось сохранить видео. Проверьте соединение и повторите."; }
            final String notice = message;
            runOnUiThread(() -> { if (!isDestroyed()) Toast.makeText(this, notice, Toast.LENGTH_LONG).show(); });
        });
    }

    @Override public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        if (offlineScreen != null) return;
        if (web == null || mainFrameFailed) return;
        String url = web.getUrl();
        if (url != null && allowedPage(Uri.parse(url))) web.loadUrl(withMode(url, bookPage(Uri.parse(url).getPath())));
    }
    @Override public void onBackPressed() {
        if (customVideo != null) { closeCustomVideo(); return; }
        if (offlineReader != null) { showOfflineShelf(); return; }
        if (offlineScreen != null) { closeOfflineScreen(); return; }
        if (mainFrameFailed) { super.onBackPressed(); return; }
        String url = web == null ? null : web.getUrl();
        if (url != null && (bookPage(Uri.parse(url).getPath()) || url.contains("/app/login/")
            || url.contains("/privacy/") || url.contains("/app/account/delete/"))) openShelf();
        else super.onBackPressed();
    }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putString("portal_origin", ORIGIN);
        if (web != null && !mainFrameFailed) {
            try { web.saveState(state); }
            catch (RuntimeException error) { Log.w(TAG, "Could not save WebView state", error); }
        }
        super.onSaveInstanceState(state);
    }
    @Override protected void onPause() {
        resumed = false;
        updateScreenAwake();
        if (web != null) {
            try {
                web.evaluateJavascript("window.dispatchEvent(new Event('pagehide'));document.querySelectorAll('audio,video').forEach(a=>a.pause());", null);
                web.onPause();
            } catch (RuntimeException error) { Log.w(TAG, "Could not pause WebView", error); }
        }
        super.onPause();
    }
    @Override protected void onResume() {
        super.onResume(); resumed = true;
        if (web != null) {
            web.onResume();
            try { web.evaluateJavascript("window.dispatchEvent(new Event('pageshow'));", null); }
            catch (RuntimeException ignored) { }
        }
        updateScreenAwake();
    }
    @Override protected void onDestroy() {
        accountGeneration++;
        closeCustomVideo();
        closeOfflineScreen();
        downloads.shutdownNow();
        destroyWebView();
        super.onDestroy();
    }
}
