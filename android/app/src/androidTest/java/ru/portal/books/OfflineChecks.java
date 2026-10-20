package ru.portal.books;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.pdf.PdfDocument;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;

/** Device checks using Android's own runner; this class is absent from release APKs. */
public final class OfflineChecks extends Instrumentation {
    private Activity activity;
    private Object store;
    private Class<?> storeClass;
    private Context target;
    private File directory;
    private final StringBuilder report = new StringBuilder();

    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            target = getTargetContext();
            check("ranchu".equals(android.os.Build.HARDWARE) || "goldfish".equals(android.os.Build.HARDWARE), "This runner only changes data on a test emulator.");
        } catch (Throwable error) {
            finishError(result, error);
            return;
        }
        try {
            target = getTargetContext();
            storeClass = Class.forName("ru.portal.books.OfflineBooks", true, target.getClassLoader());
            Constructor<?> constructor = storeClass.getDeclaredConstructor(Context.class);
            constructor.setAccessible(true);
            store = constructor.newInstance(target);
            method(storeClass, "clear").invoke(store);
            target.getSharedPreferences("MainActivity", Context.MODE_PRIVATE).edit().clear().commit();
            Intent launch = new Intent().setClassName(target.getPackageName(), "ru.portal.books.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity = startActivitySync(launch);
            directory = new File(target.getFilesDir(), "offline-books");

            urlBoundaries();
            seed(false);
            File metadata = new File(directory, "books.json");
            check(metadata.renameTo(new File(directory, "books.json.bak")), "Could not stage an interrupted atomic index write.");
            check(list("guest").size() == 1 && metadata.isFile(), "AtomicFile must restore the last committed index after interruption.");
            passed("Interrupted metadata writes recover from AtomicFile's committed backup.");
            check(list("guest").size() == 1 && list("22").size() == 1, "Account-scoped lists must not mix files.");
            runOnMainSync(() -> invokeActivity("showOfflineShelf", new Class<?>[0]));
            drainDownloads();
            waitForIdleSync();
            final boolean[] visible = new boolean[2];
            runOnMainSync(() -> {
                visible[0] = containsText(activity.getWindow().getDecorView(), "Offline fixture");
                visible[1] = containsText(activity.getWindow().getDecorView(), "Private fixture");
            });
            check(visible[0] && !visible[1], "Guest offline shelf exposed a different account's book.");
            passed("Guest and personal offline shelves are isolated.");
            Object readable = list("guest").get(0);
            Class<?> readableClass = Class.forName("ru.portal.books.OfflineBooks$Book", true, target.getClassLoader());
            runOnMainSync(() -> invokeActivity("openOfflineBook", new Class<?>[]{readableClass}, readable));
            final int screenFlag = android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON;
            runOnMainSync(() -> check((activity.getWindow().getAttributes().flags & screenFlag) != 0, "PDF reading must keep the screen on."));
            runOnMainSync(() -> { callActivityOnPause(activity); check((activity.getWindow().getAttributes().flags & screenFlag) == 0, "Background app must release the screen."); });
            runOnMainSync(() -> { callActivityOnResume(activity); check((activity.getWindow().getAttributes().flags & screenFlag) != 0, "Returning to reading must restore screen-on."); });
            runOnMainSync(() -> { invokeActivity("showOfflineShelf", new Class<?>[0]); check((activity.getWindow().getAttributes().flags & screenFlag) == 0, "A downloads list must allow sleep."); });
            passed("PDF reading keeps screen on; pause/list release it and resume restores it.");
            Class<?> downloader = Class.forName("ru.portal.books.VideoDownload", true, target.getClassLoader());
            Method allowedVideo = method(downloader, "allowed", Uri.class);
            check((boolean) allowedVideo.invoke(null, Uri.parse("https://qicko.ru/books/example/video/file/?download=1")), "Expected MP4 endpoint.");
            for (String url : new String[]{"http://qicko.ru/books/example/video/file/", "https://other.test/books/example/video/file/", "https://qicko.ru/books/example/video/file/?next=evil", "https://qicko.ru:444/books/example/video/file/"})
                check(!(boolean) allowedVideo.invoke(null, Uri.parse(url)), "Unsafe video address accepted.");
            passed("Native MP4 export rejects foreign origins, HTTP, unexpected ports and parameters.");

            runOnMainSync(() -> invokeActivity("updateOfflineAccount", new Class<?>[]{String.class, String.class}, "22", "fixture@example.test"));
            drainDownloads();
            check(list("guest").isEmpty() && list("22").isEmpty() && countPdfFiles() == 0, "Account switch must erase previous local copies.");
            passed("Switching accounts removes metadata and PDF copies.");

            seed(false);
            runOnMainSync(() -> invokeActivity("forgetOfflineAccount", new Class<?>[0]));
            drainDownloads();
            check(list("guest").isEmpty() && list("22").isEmpty() && countPdfFiles() == 0, "Logout left offline copies on device.");
            check(!target.getSharedPreferences("MainActivity", Context.MODE_PRIVATE).contains("offline_account"), "Logout persisted the previous owner.");
            passed("Logout removes files, metadata and the stored account context.");

            seed(true);
            Object broken = list("guest").get(0);
            Class<?> bookClass = Class.forName("ru.portal.books.OfflineBooks$Book", true, target.getClassLoader());
            runOnMainSync(() -> invokeActivity("openOfflineBook", new Class<?>[]{bookClass}, broken));
            long deadline = System.currentTimeMillis() + 5000;
            boolean[] refused = new boolean[1];
            while (System.currentTimeMillis() < deadline && !refused[0]) {
                runOnMainSync(() -> refused[0] = containsText(activity.getWindow().getDecorView(), "Не удалось открыть PDF"));
                if (!refused[0]) Thread.sleep(50);
            }
            check(refused[0], "A corrupt PDF must show a recovery message without closing the app.");
            passed("Corrupt PDF reports an error and the native screen stays alive.");

            runOnMainSync(() -> invokeActivity("closeOfflineScreen", new Class<?>[0]));
            method(storeClass, "clear").invoke(store);
            runOnMainSync(() -> activity.finish());
            result.putString("stream", "\nALL OFFLINE CHECKS PASSED\n" + report);
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) { finishError(result, error); }
    }

    private void urlBoundaries() throws Exception {
        Method permitted = method(storeClass, "pdfAddress", Uri.class);
        check((boolean) permitted.invoke(null, Uri.parse("https://qicko.ru/books/example/download/pdf/")), "Expected same-origin PDF address.");
        String[] rejected = {
            "http://qicko.ru/books/example/download/pdf/", "https://qicko.ru.evil.test/books/example/download/pdf/",
            "https://user@qicko.ru/books/example/download/pdf/", "https://qicko.ru:444/books/example/download/pdf/",
            "https://other.test/books/example/download/pdf/", "https://qicko.ru/books/example/download/pdf/?next=other",
            "https://qicko.ru/books/example/download/mp3/", "file:///books/example/download/pdf/"
        };
        for (String address : rejected) check(!(boolean) permitted.invoke(null, Uri.parse(address)), "Unsafe PDF URL accepted: " + address);
        passed("PDF source accepts only the exact HTTPS origin and the PDF export endpoint.");
    }

    private void seed(boolean corrupt) throws Exception {
        if (!directory.isDirectory()) check(directory.mkdirs(), "Could not create private fixture directory.");
        String id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        File publicPdf = new File(directory, id + "-1.pdf");
        if (corrupt) {
            try (FileOutputStream output = new FileOutputStream(publicPdf)) { output.write("%PDF-1.7\ncorrupt fixture".getBytes(StandardCharsets.US_ASCII)); }
        } else writePdf(publicPdf);
        JSONArray records = new JSONArray();
        records.put(record(id, corrupt ? "Broken fixture" : "Offline fixture", "guest", publicPdf.getName()));
        if (!corrupt) {
            String privateId = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
            File privatePdf = new File(directory, privateId + "-2.pdf");
            writePdf(privatePdf);
            records.put(record(privateId, "Private fixture", "22", privatePdf.getName()));
        }
        try (FileOutputStream output = new FileOutputStream(new File(directory, "books.json"))) { output.write(records.toString().getBytes(StandardCharsets.UTF_8)); }
    }

    private JSONObject record(String id, String title, String owner, String file) throws Exception {
        return new JSONObject().put("id", id).put("title", title).put("owner", owner).put("file", file).put("savedAt", System.currentTimeMillis()).put("page", 0);
    }

    private void writePdf(File file) throws Exception {
        PdfDocument pdf = new PdfDocument();
        try {
            for (int number = 1; number <= 3; number++) {
                PdfDocument.Page page = pdf.startPage(new PdfDocument.PageInfo.Builder(420, 595, number).create());
                page.getCanvas().drawColor(number == 1 ? Color.rgb(255,240,210) : number == 2 ? Color.rgb(210,235,255) : Color.rgb(235,220,255));
                Paint paint = new Paint();
                paint.setTextSize(34);
                paint.setColor(Color.rgb(43,63,67));
                page.getCanvas().drawText("Offline page " + number, 50, 150, paint);
                pdf.finishPage(page);
            }
            try (FileOutputStream output = new FileOutputStream(file)) { pdf.writeTo(output); }
        } finally { pdf.close(); }
    }

    @SuppressWarnings("unchecked") private List<Object> list(String owner) throws Exception { return (List<Object>) method(storeClass, "list", String.class).invoke(store, owner); }
    private int countPdfFiles() { File[] files = directory.listFiles((dir, name) -> name.endsWith(".pdf")); return files == null ? 0 : files.length; }
    private void drainDownloads() throws Exception {
        Field field = activity.getClass().getDeclaredField("downloads");
        field.setAccessible(true);
        ((ExecutorService) field.get(activity)).submit(() -> { }).get(10, TimeUnit.SECONDS);
        waitForIdleSync();
    }
    private static Method method(Class<?> type, String name, Class<?>... parameters) throws Exception { Method result = type.getDeclaredMethod(name, parameters); result.setAccessible(true); return result; }
    private void invokeActivity(String name, Class<?>[] types, Object... arguments) {
        try { method(activity.getClass(), name, types).invoke(activity, arguments); }
        catch (Exception error) { throw new IllegalStateException(error); }
    }
    private static boolean containsText(View view, String text) {
        if (view instanceof TextView && ((TextView) view).getText().toString().contains(text)) return true;
        if (view instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++) if (containsText(((ViewGroup) view).getChildAt(i), text)) return true;
        return false;
    }
    private static void check(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    private void passed(String message) { report.append("PASS: ").append(message).append('\n'); }
    private void finishError(Bundle result, Throwable error) {
        Log.e("PortalOfflineChecks", "Device check failed", error);
        result.putString("stream", "\nOFFLINE CHECK FAILED\n" + Log.getStackTraceString(error) + "\n" + report);
        if (activity != null) runOnMainSync(() -> activity.finish());
        finish(Activity.RESULT_CANCELED, result);
    }
}
