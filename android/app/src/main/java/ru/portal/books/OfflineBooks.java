package ru.portal.books;

import android.content.Context;
import android.graphics.pdf.PdfRenderer;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.util.AtomicFile;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.BooleanSupplier;

/** Private, account-scoped PDF downloads. No cookies or credentials are written to disk. */
final class OfflineBooks {
    private static final long MAX_BYTES = 100L * 1024 * 1024;
    private final File directory;
    private final AtomicFile index;

    static final class Book {
        final String id;
        final String title;
        final String owner;
        final long savedAt;
        final File file;
        int page;

        Book(String id, String title, String owner, long savedAt, File file, int page) {
            this.id = id;
            this.title = title;
            this.owner = owner;
            this.savedAt = savedAt;
            this.file = file;
            this.page = page;
        }
    }

    OfflineBooks(Context context) {
        directory = new File(context.getFilesDir(), "offline-books");
        index = new AtomicFile(new File(directory, "books.json"));
    }

    static boolean sameServer(Uri uri) {
        return "https".equalsIgnoreCase(uri.getScheme()) && "qicko.ru".equalsIgnoreCase(uri.getHost())
            && uri.getUserInfo() == null && (uri.getPort() == -1 || uri.getPort() == 443);
    }

    static boolean pdfAddress(Uri uri) {
        String path = uri.getPath();
        return sameServer(uri) && path != null && path.matches("/books/[A-Za-z0-9_-]+/download/pdf/")
            && uri.getQuery() == null && uri.getFragment() == null;
    }

    synchronized List<Book> list(String owner) throws IOException {
        List<Book> result = read();
        result.removeIf(book -> !book.owner.equals(owner) || !book.file.isFile());
        result.sort(Comparator.comparingLong((Book book) -> book.savedAt).reversed());
        return result;
    }

    Book download(String address, String title, String owner, String cookies, BooleanSupplier stillAuthorized) throws IOException {
        if (!pdfAddress(Uri.parse(address))) throw new IOException("Недопустимый адрес PDF.");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("Не удалось создать папку книг.");
        File temporary = File.createTempFile("download-", ".part", directory);
        HttpURLConnection connection = null;
        try {
            Uri target = Uri.parse(address);
            for (int redirects = 0; ; redirects++) {
                if (!sameServer(target)) throw new IOException("Переход на другой сайт запрещён.");
                if (!stillAuthorized.getAsBoolean() || Thread.currentThread().isInterrupted()) throw new IOException("Сохранение отменено.");
                connection = (HttpURLConnection) new URL(target.toString()).openConnection();
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(15000);
                connection.setReadTimeout(20000);
                connection.setRequestProperty("Accept", "application/pdf");
                connection.setRequestProperty("Accept-Encoding", "identity");
                if (cookies != null && !cookies.isEmpty()) connection.setRequestProperty("Cookie", cookies);
                int status = connection.getResponseCode();
                if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                    String location = connection.getHeaderField("Location");
                    if (redirects >= 3 || location == null) throw new IOException("Слишком много перенаправлений.");
                    target = Uri.parse(new URL(new URL(target.toString()), location).toString());
                    connection.disconnect();
                    connection = null;
                    continue;
                }
                if (status != 200) throw new IOException(status == 401 || status == 403 ? "Войдите в аккаунт с доступом к этой книге." : "Сервер не смог подготовить PDF (" + status + ").");
                String type = connection.getContentType();
                if (type == null || !"application/pdf".equalsIgnoreCase(type.split(";", 2)[0].trim())) throw new IOException("Сервер не вернул PDF. Проверьте вход и доступ к книге.");
                long length = connection.getContentLengthLong();
                if (length > MAX_BYTES) throw new IOException("PDF больше 100 МБ. Уменьшите размер книги.");
                if (directory.getUsableSpace() < Math.max(length, 0) + 8L * 1024 * 1024) throw new IOException("Недостаточно свободного места.");
                try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(temporary)) {
                    byte[] buffer = new byte[32768];
                    long total = 0;
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (!stillAuthorized.getAsBoolean() || Thread.currentThread().isInterrupted()) throw new IOException("Сохранение отменено.");
                        total += count;
                        if (total > MAX_BYTES) throw new IOException("PDF больше 100 МБ.");
                        output.write(buffer, 0, count);
                    }
                    output.getFD().sync();
                }
                break;
            }
            byte[] magic = new byte[5];
            try (FileInputStream input = new FileInputStream(temporary)) {
                if (input.read(magic) != 5 || !"%PDF-".equals(new String(magic, StandardCharsets.US_ASCII))) throw new IOException("Файл не является PDF.");
            }
            try (ParcelFileDescriptor descriptor = ParcelFileDescriptor.open(temporary, ParcelFileDescriptor.MODE_READ_ONLY);
                 PdfRenderer renderer = new PdfRenderer(descriptor)) {
                if (renderer.getPageCount() < 1) throw new IOException("В PDF нет страниц.");
            } catch (RuntimeException error) {
                throw new IOException("Не удалось открыть PDF. Файл повреждён или защищён паролем.", error);
            }
            synchronized (this) {
                if (!stillAuthorized.getAsBoolean()) throw new IOException("Сохранение отменено после выхода из аккаунта.");
                String id = digest(owner + "\n" + address);
                List<Book> books = read();
                Book previous = null;
                for (Book book : books) if (book.id.equals(id)) previous = book;
                File destination = new File(directory, id + "-" + System.nanoTime() + ".pdf");
                if (!temporary.renameTo(destination)) throw new IOException("Не удалось сохранить PDF.");
                Book result = new Book(id, title, owner, System.currentTimeMillis(), destination, previous == null ? 0 : previous.page);
                books.removeIf(book -> book.id.equals(id));
                books.add(result);
                try { write(books); }
                catch (IOException error) { destination.delete(); throw error; }
                if (previous != null) previous.file.delete();
                return result;
            }
        } finally {
            if (connection != null) connection.disconnect();
            temporary.delete();
        }
    }

    synchronized void setPage(Book selected, int page) throws IOException {
        List<Book> books = read();
        for (Book book : books) if (book.id.equals(selected.id)) book.page = Math.max(0, page);
        write(books);
    }

    synchronized void delete(Book selected) throws IOException {
        List<Book> books = read();
        books.removeIf(book -> book.id.equals(selected.id));
        write(books);
        if (selected.file.exists() && !selected.file.delete()) throw new IOException("Не удалось удалить файл книги.");
    }

    synchronized void clear() throws IOException {
        if (!directory.exists()) return;
        File[] files = directory.listFiles();
        if (files == null) throw new IOException("Не удалось прочитать папку книг.");
        for (File file : files) if (file.isFile() && !file.delete()) throw new IOException("Не удалось удалить сохранённую книгу.");
        index.delete();
    }

    private List<Book> read() throws IOException {
        List<Book> books = new ArrayList<>();
        try (InputStream input = index.openRead(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (bytes.size() + count > 2 * 1024 * 1024) throw new IOException("Повреждён список книг.");
                bytes.write(buffer, 0, count);
            }
            JSONArray values = new JSONArray(bytes.toString(StandardCharsets.UTF_8.name()));
            for (int i = 0; i < values.length(); i++) {
                JSONObject value = values.getJSONObject(i);
                String name = value.getString("file");
                if (!name.matches("[a-f0-9]{64}-[0-9]+\\.pdf")) continue;
                books.add(new Book(value.getString("id"), value.getString("title"), value.getString("owner"), value.getLong("savedAt"), new File(directory, name), value.optInt("page", 0)));
            }
            return books;
        } catch (FileNotFoundException absent) { return books; }
        catch (JSONException error) { throw new IOException("Повреждён список книг.", error); }
    }

    private void write(List<Book> books) throws IOException {
        JSONArray values = new JSONArray();
        try {
            for (Book book : books) {
                JSONObject value = new JSONObject();
                value.put("id", book.id).put("title", book.title).put("owner", book.owner).put("savedAt", book.savedAt).put("file", book.file.getName()).put("page", book.page);
                values.put(value);
            }
        } catch (JSONException error) { throw new IOException(error); }
        FileOutputStream output = null;
        try {
            output = index.startWrite();
            output.write(values.toString().getBytes(StandardCharsets.UTF_8));
            index.finishWrite(output);
        } catch (IOException error) { if (output != null) index.failWrite(output); throw error; }
    }

    private static String digest(String text) throws IOException {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder();
            for (byte value : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            return result.toString();
        } catch (NoSuchAlgorithmException error) { throw new IOException(error); }
    }
}
