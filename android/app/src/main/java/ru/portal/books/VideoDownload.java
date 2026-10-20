package ru.portal.books;

import android.content.Context;
import android.net.Uri;
import android.provider.DocumentsContract;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Explicit Storage Access Framework export; no shared-storage permissions. */
final class VideoDownload {
    static boolean allowed(Uri uri) {
        return OfflineBooks.sameServer(uri) && uri.getPath() != null
            && uri.getPath().matches("/books/[A-Za-z0-9_-]+/video/file/")
            && (uri.getQuery() == null || "download=1".equals(uri.getQuery())) && uri.getFragment() == null;
    }
    static void save(Context context, Uri destination, String address, String cookies) throws IOException {
        if (!allowed(Uri.parse(address))) throw new IOException("Недопустимый адрес видео.");
        HttpURLConnection connection = null;
        boolean complete = false;
        try {
            // The file endpoint does not redirect. Never forward cookies to a redirect.
            connection = (HttpURLConnection) new URL(address).openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("Accept", "video/mp4");
            if (cookies != null) connection.setRequestProperty("Cookie", cookies);
            if (connection.getResponseCode() != 200 || connection.getContentType() == null
                || !connection.getContentType().split(";", 2)[0].trim().equalsIgnoreCase("video/mp4")) {
                throw new IOException("Видео недоступно. Дождитесь сборки и проверьте доступ к книге.");
            }
            long maximum = 1024L * 1024 * 1024;
            if (connection.getContentLengthLong() > maximum) throw new IOException("Видео больше 1 ГБ.");
            try (DataInputStream input = new DataInputStream(connection.getInputStream())) {
                byte[] header = new byte[12];
                input.readFully(header);
                if (!"ftyp".equals(new String(header, 4, 4, StandardCharsets.US_ASCII))) throw new IOException("Сервер не вернул MP4.");
                try (OutputStream output = context.getContentResolver().openOutputStream(destination, "w")) {
                    if (output == null) throw new IOException("Не удалось открыть выбранный файл.");
                    output.write(header);
                    byte[] buffer = new byte[65536];
                    long total = header.length;
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        total += count;
                        if (total > maximum || Thread.currentThread().isInterrupted()) throw new IOException("Сохранение видео остановлено.");
                        output.write(buffer, 0, count);
                    }
                    output.flush();
                    complete = true;
                }
            }
        } finally {
            if (connection != null) connection.disconnect();
            if (!complete) {
                try { DocumentsContract.deleteDocument(context.getContentResolver(), destination); }
                catch (Exception ignored) { }
            }
        }
    }
}
